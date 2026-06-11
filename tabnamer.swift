// tabnamer — reads a prompt on stdin, returns a short tab label produced by
// Apple's on-device Foundation Models. Exits non-zero if the model is
// unavailable or errors, so the caller can skip naming.
import FoundationModels
import Foundation

let data = FileHandle.standardInput.readDataToEndOfFile()
guard let prompt = String(data: data, encoding: .utf8),
      !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
    exit(1)
}

let model = SystemLanguageModel.default
guard case .available = model.availability else {
    FileHandle.standardError.write(Data("model unavailable\n".utf8))
    exit(2)
}

do {
    let session = LanguageModelSession()
    let response = try await session.respond(to: prompt)
    print(response.content)
} catch {
    FileHandle.standardError.write(Data("error: \(error)\n".utf8))
    exit(3)
}
