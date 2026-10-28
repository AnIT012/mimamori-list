// 変換した JSON を、本物の WebKit（WKContentRuleListStore）に読ませる。1つでも断られたら終了コード 1。
// 使い方: swift compile_check.swift a.json b.json ...
// macOS の WebKit は iPhone と同じ Content Blocker の読み取りを使う。
import Foundation
import WebKit

@MainActor
func check(_ paths: [String]) async -> Bool {
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent("compile-\(UUID().uuidString)")
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: dir) }
    guard let store = WKContentRuleListStore(url: dir) else { print("WebKit の置き場を作れません"); return false }
    var ok = true
    for path in paths {
        guard let json = try? String(contentsOfFile: path, encoding: .utf8) else { print("✗ 読めない \(path)"); ok = false; continue }
        let start = Date()
        do {
            _ = try await store.compileContentRuleList(forIdentifier: (path as NSString).lastPathComponent, encodedContentRuleList: json)
            print(String(format: "✓ %@ %.1f 秒", path, Date().timeIntervalSince(start)))
        } catch {
            print("✗ \(path): \((error as NSError).userInfo)")
            ok = false
        }
    }
    return ok
}

let ok = await check(Array(CommandLine.arguments.dropFirst()))
exit(ok ? 0 : 1)
