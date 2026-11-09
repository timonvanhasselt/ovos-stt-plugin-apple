// apple-stt-helper
//
// Modes:
//   --list                         list locales + on-device support
//   --auth                         request/print speech authorization status
//   --file X.wav [--lang en-US] [--server]   one-shot recognition
//   --serve                        persistent mode, JSON lines on stdin/stdout
//
// Serve protocol (one JSON object per line):
//   -> {"id":"1","cmd":"recognize","file":"/tmp/a.wav","lang":"en-US","server":true,"timeout":30}
//   <- {"id":"1","text":"...","code":0}   or   {"id":"1","error":"...","code":4}
//   -> {"id":"2","cmd":"languages"}   <- {"id":"2","languages":[{"id":"nl-NL","on_device":false},...]}
//   -> {"id":"3","cmd":"ping"}        <- {"id":"3","pong":true}
//   First line emitted by the helper: {"ready":true,"auth":3}

import Foundation
import Speech

let stdoutLock = NSLock()

func emit(_ obj: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: obj, options: []) else { return }
    stdoutLock.lock()
    defer { stdoutLock.unlock() }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}

func printErr(_ message: String) {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
}

func arg(_ name: String) -> String? {
    guard let i = CommandLine.arguments.firstIndex(of: name),
          i + 1 < CommandLine.arguments.count else { return nil }
    return CommandLine.arguments[i + 1]
}

final class Job {
    var finished = false
    var task: SFSpeechRecognitionTask?
}

var recognizers: [String: SFSpeechRecognizer] = [:]

func recognizer(for lang: String) -> SFSpeechRecognizer? {
    if let r = recognizers[lang] { return r }
    guard let r = SFSpeechRecognizer(locale: Locale(identifier: lang)) else { return nil }
    recognizers[lang] = r
    return r
}

/// Must be called on the main queue.
func recognize(file: String, lang: String, server: Bool, timeout: TimeInterval,
               completion: @escaping (_ text: String?, _ error: String?, _ code: Int32) -> Void) {
    guard FileManager.default.fileExists(atPath: file) else {
        completion(nil, "file not found: \(file)", 2); return
    }
    guard let rec = recognizer(for: lang) else {
        completion(nil, "locale not supported: \(lang)", 3); return
    }
    if !server && !rec.supportsOnDeviceRecognition {
        completion(nil, "no on-device model for \(lang) (allow server to use Apple servers)", 4); return
    }
    if !rec.isAvailable {
        completion(nil, "recognizer not available for \(lang) (offline?)", 7); return
    }

    let request = SFSpeechURLRecognitionRequest(url: URL(fileURLWithPath: file))
    request.requiresOnDeviceRecognition = !server
    request.shouldReportPartialResults = false

    let job = Job()
    func finish(_ text: String?, _ err: String?, _ code: Int32) {
        if job.finished { return }
        job.finished = true
        job.task = nil
        completion(text, err, code)
    }

    job.task = rec.recognitionTask(with: request) { result, error in
        DispatchQueue.main.async {
            if let result = result, result.isFinal {
                finish(result.bestTranscription.formattedString, nil, 0)
                return
            }
            if let error = error {
                let ns = error as NSError
                if ns.code == 1110 {  // "No speech detected"
                    finish("", nil, 0)
                } else {
                    finish(nil, error.localizedDescription, 1)
                }
            }
        }
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + timeout) {
        finish(nil, "timeout", 6)
    }
}

func handle(line: String) {
    guard let data = line.data(using: .utf8),
          let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
        emit(["error": "invalid json", "code": 2]); return
    }
    let id = (obj["id"] as? String) ?? ""
    let cmd = (obj["cmd"] as? String) ?? "recognize"

    switch cmd {
    case "ping":
        emit(["id": id, "pong": true])
    case "languages":
        let langs = SFSpeechRecognizer.supportedLocales()
            .map { $0.identifier.replacingOccurrences(of: "_", with: "-") }
            .sorted()
            .map { l -> [String: Any] in
                ["id": l, "on_device": recognizer(for: l)?.supportsOnDeviceRecognition ?? false]
            }
        emit(["id": id, "languages": langs])
    case "recognize":
        guard let file = obj["file"] as? String else {
            emit(["id": id, "error": "missing file", "code": 2]); return
        }
        let lang = (obj["lang"] as? String) ?? "en-US"
        let server = (obj["server"] as? Bool) ?? false
        let timeout = (obj["timeout"] as? Double) ?? 30
        recognize(file: file, lang: lang, server: server, timeout: timeout) { text, err, code in
            if let err = err {
                emit(["id": id, "error": err, "code": Int(code)])
            } else {
                emit(["id": id, "text": text ?? "", "code": 0])
            }
        }
    default:
        emit(["id": id, "error": "unknown cmd \(cmd)", "code": 2])
    }
}

// ---------- entry ----------

let args = CommandLine.arguments

if args.contains("--list") {
    for l in SFSpeechRecognizer.supportedLocales().sorted(by: { $0.identifier < $1.identifier }) {
        let r = SFSpeechRecognizer(locale: l)
        print(l.identifier, r?.supportsOnDeviceRecognition ?? false)
    }
    exit(0)
}

if args.contains("--auth") {
    print("status before:", SFSpeechRecognizer.authorizationStatus().rawValue)
    SFSpeechRecognizer.requestAuthorization { s in
        print("status after:", s.rawValue)
        exit(0)
    }
    RunLoop.main.run(until: Date().addingTimeInterval(30))
    printErr("no response within 30s")
    exit(7)
}

if args.contains("--serve") {
    SFSpeechRecognizer.requestAuthorization { status in
        DispatchQueue.main.async {
            emit(["ready": status == .authorized, "auth": status.rawValue])
            if status != .authorized { exit(5) }
            let reader = Thread {
                while let line = readLine() {
                    let trimmed = line.trimmingCharacters(in: .whitespaces)
                    if trimmed.isEmpty { continue }
                    DispatchQueue.main.async { handle(line: trimmed) }
                }
                exit(0)  // stdin closed -> parent gone
            }
            reader.start()
        }
    }
    RunLoop.main.run()
}

// one-shot
guard let file = arg("--file") else {
    printErr("usage: apple-stt-helper --file x.wav [--lang en-US] [--server] | --serve | --list | --auth")
    exit(2)
}
let lang = arg("--lang") ?? "en-US"
let useServer = args.contains("--server")

SFSpeechRecognizer.requestAuthorization { status in
    DispatchQueue.main.async {
        guard status == .authorized else {
            printErr("speech permission denied (status \(status.rawValue))")
            exit(5)
        }
        recognize(file: file, lang: lang, server: useServer, timeout: 60) { text, err, code in
            if let err = err { printErr(err); exit(code) }
            print(text ?? "")
            exit(0)
        }
    }
}
RunLoop.main.run()
