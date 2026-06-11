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

let data = FileHandle.standardInput.readDataToEndOfFile()
guard let input = String(data: data, encoding: .utf8),
      !input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
    exit(1)
}

let sentinel = "<<<PROMPT>>>"
let instructionsText: String
let promptText: String
if let r = input.range(of: sentinel) {
    instructionsText = String(input[..<r.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
    promptText = String(input[r.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
} else {
    instructionsText = ""
    promptText = input.trimmingCharacters(in: .whitespacesAndNewlines)
}
guard !promptText.isEmpty else { exit(1) }

let model = SystemLanguageModel.default
guard case .available = model.availability else {
    FileHandle.standardError.write(Data("model unavailable\n".utf8))
    exit(2)
}

do {
    let session: LanguageModelSession
    if instructionsText.isEmpty {
        session = LanguageModelSession()
    } else {
        session = LanguageModelSession { instructionsText }
    }
    let options = GenerationOptions(temperature: 0.3)
    let response = try await session.respond(to: promptText, options: options)
    print(response.content)
} catch {
    FileHandle.standardError.write(Data("error: \(error)\n".utf8))
    exit(3)
}
