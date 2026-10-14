import Foundation

// What the server sends (server/app.py). Fields the app doesn't need are left out;
// anything that can be missing is optional, so a server change never breaks decoding.

struct Leagues: Decodable {
    let updated: String
    let leagues: [League]
}

struct League: Decodable, Identifiable, Hashable {
    let id: String
    let name: String
    let types: [String]
    let weeks: [Week]
}

struct Week: Decodable, Hashable {
    let week: Int
    let label: String
}

struct Board: Decodable {
    let league: String
    let week: Int
    let label: String
    let type: String
    let updated: String
    let note: String?
    let modelNote: String?
    let starters: String?
    let singles: [Bet]
    let parlays: [Parlay]
    let money: [Parlay]
    let locked: Bool
    let lockedCounts: LockedCounts?
}

struct LockedCounts: Decodable {
    let singles: Int
    let parlays: Int
    let money: Int
}

struct Bet: Decodable, Identifiable {
    let id: String
    let label: String
    let market: String?
    let game: String?
    let when: String?
    let prob: Double
    let breakeven: String
    let note: String?
    let confirm: Bool?
    let hist: History?
    let tier: Tier?
    let league: String?

    /// What $10 returns at the break-even odds, stake included.
    var paysOn10: Double { prob > 0 ? 10 / prob : 0 }
}

struct History: Decodable {
    let now: [Int]?
    let last: [Int]?

    /// "4/5 this season", or last season's when there's none yet.
    var text: String? {
        if let n = now, n.count == 2, n[1] > 0 { return "\(n[0])/\(n[1]) this season" }
        if let l = last, l.count == 2, l[1] > 0 { return "\(l[0])/\(l[1]) last season" }
        return nil
    }
}

struct Tier: Decodable {
    let tier: String
    let word: String
}

struct Parlay: Decodable, Identifiable {
    let legs: [Bet]
    let prob: Double
    let breakeven: String
    let back: Double?  // Money Maker: what FanDuel would pay back per $1, about

    var id: String { legs.map(\.id).joined(separator: " + ") }
    var paysOn10: Double { (back ?? (prob > 0 ? 1 / prob : 0)) * 10 }
}

struct MultiLays: Decodable {
    let locked: Bool
    let parlays: [Parlay]
    let lockedCount: Int?
    let updated: String
}

struct Record: Decodable {
    let updated: String
    let won: Int
    let lost: Int
    let pending: Int
    let recent: [RecordEntry]
}

struct RecordEntry: Decodable, Identifiable {
    let id: String
    let kind: String
    let sport: String?
    let status: String
    let prob: Double
    let created: String
    let legs: [RecordLeg]
}

struct RecordLeg: Decodable {
    let pick: String
    let game: String?
}
