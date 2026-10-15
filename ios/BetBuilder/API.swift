import Foundation

/// Talks to the Bet Builder server. Without a server address (or when started with
/// -demo) it reads the sample answers bundled in Demo/, for previews and screenshots.
final class API {
    static let shared = API()

    let baseURL: URL?
    let demo: Bool
    /// Who's asking: RevenueCat's user id, so the server can check the subscription.
    var userID: String?

    private init() {
        let text = (Bundle.main.object(forInfoDictionaryKey: "BetAPIURL") as? String) ?? ""
        baseURL = URL(string: text.trimmingCharacters(in: .whitespaces))
        demo = baseURL == nil || text.isEmpty || ProcessInfo.processInfo.arguments.contains("-demo")
    }

    enum Failure: LocalizedError {
        case notReady, server(Int), offline
        var errorDescription: String? {
            switch self {
            case .notReady: return "Bets are being built. Try again in a minute."
            case .server(let code): return "The server had a problem (\(code)). Try again soon."
            case .offline: return "Couldn't reach Bet Builder. Check your connection."
            }
        }
    }

    func leagues() async throws -> Leagues { try await get("/v1/leagues", demo: "leagues") }

    func board(_ league: String, week: Int?, type: String) async throws -> Board {
        var path = "/v1/board/\(league)?type=\(type.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? type)"
        if let week { path += "&week=\(week)" }
        return try await get(path, demo: "board-\(league)")
    }

    func multi() async throws -> MultiLays { try await get("/v1/multi", demo: "multi") }

    func value(_ league: String, kind: String) async throws -> ValueBoard {
        try await get("/v1/value/\(league)?kind=\(kind)", demo: "value-\(league)")
    }

    func record() async throws -> Record { try await get("/v1/record?limit=100", demo: "record") }

    private func get<T: Decodable>(_ path: String, demo name: String) async throws -> T {
        if demo {
            guard let url = Bundle.main.url(forResource: name, withExtension: "json") else {
                throw Failure.notReady
            }
            return try JSONDecoder().decode(T.self, from: Data(contentsOf: url))
        }
        guard let base = baseURL, let url = URL(string: base.absoluteString + path) else {
            throw Failure.offline
        }
        var request = URLRequest(url: url, timeoutInterval: 30)
        if let userID { request.setValue(userID, forHTTPHeaderField: "X-User-ID") }
        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw Failure.offline
        }
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        if code == 503 { throw Failure.notReady }
        guard code == 200 else { throw Failure.server(code) }
        return try JSONDecoder().decode(T.self, from: data)
    }
}
