// tabnamer — reads instructions + a prompt on stdin, returns a short tab label
// produced by Apple's on-device Foundation Models. Exits non-zero if the model
// is unavailable or errors, so the caller can skip naming.
//
// stdin protocol: the system instructions, then a line containing the sentinel
// `<<<PROMPT>>>`, then the per-tab prompt. If the sentinel is absent the whole
// input is treated as the prompt (no instructions). Instructions are delivered
// through the model's instructions channel — Apple trains the model to obey
// those over anything in the prompt — and we sample at a low temperature so the
// small model stays on-task instead of improvising.
import FoundationModels
import Foundation

// Read one length-prefixed frame from stdin: a decimal byte count, a newline,
// then exactly that many UTF-8 bytes. Returns nil on EOF.
func readFrame(_ handle: FileHandle) -> String? {
    var headerBytes = [UInt8]()
    while true {
        let b = handle.readData(ofLength: 1)
        if b.isEmpty { return nil }            // EOF
        let byte = b[b.startIndex]
        if byte == 0x0A { break }              // newline ends the header
        headerBytes.append(byte)
    }
    guard let header = String(bytes: headerBytes, encoding: .utf8),
          let n = Int(header.trimmingCharacters(in: .whitespaces)), n >= 0 else {
        return nil
    }
    var body = Data()
    while body.count < n {
        let chunk = handle.readData(ofLength: n - body.count)
        if chunk.isEmpty { return nil }        // truncated
        body.append(chunk)
    }
    return String(data: body, encoding: .utf8)
}

func writeFrame(_ text: String) {
    let body = Data(text.utf8)
    let header = Data("\(body.count)\n".utf8)
    FileHandle.standardOutput.write(header)
    FileHandle.standardOutput.write(body)
}

let sentinel = "<<<PROMPT>>>"

let model = SystemLanguageModel.default
guard case .available = model.availability else {
    FileHandle.standardError.write(Data("model unavailable\n".utf8))
    exit(2)
}
// Eagerly load the model assets into memory at startup so the first naming
// request isn't paying the cold-load cost. The resident loop then keeps them
// warm for the daemon's lifetime.
LanguageModelSession().prewarm()

let stdin = FileHandle.standardInput
while let input = readFrame(stdin) {
    let trimmedAll = input.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmedAll.isEmpty {
        writeFrame("")
        continue
    }

    let instructionsText: String
    let promptText: String
    if let r = input.range(of: sentinel) {
        instructionsText = String(input[..<r.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
        promptText = String(input[r.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
    } else {
        instructionsText = ""
        promptText = trimmedAll
    }

    if promptText.isEmpty {
        writeFrame("")
        continue
    }

    do {
        // A fresh session per request keeps tabs from sharing context; the model
        // weights stay resident in the process, so there is no reload.
        let session: LanguageModelSession
        if instructionsText.isEmpty {
            session = LanguageModelSession()
        } else {
            session = LanguageModelSession { instructionsText }
        }
        let options = GenerationOptions(temperature: 0.3)
        let response = try await session.respond(to: promptText, options: options)
        writeFrame(response.content)
    } catch {
        FileHandle.standardError.write(Data("error: \(error)\n".utf8))
        writeFrame("")   // keep the loop alive; the daemon treats "" as skip
    }
}
