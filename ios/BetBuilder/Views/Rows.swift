import SwiftUI

/// A single bet: what it is, its chance, the worst odds worth taking and what $10 pays.
struct BetRow: View {
    let number: Int
    let bet: Bet

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Text("\(number)").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                .frame(width: 22, alignment: .leading)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(bet.label).font(.subheadline.weight(.semibold))
                    if let note = bet.note, !note.isEmpty { Badge(text: note) }
                    if bet.confirm == true { Badge(text: "Confirm line") }
                }
                Text([bet.market, bet.game, bet.when].compactMap { $0 }.joined(separator: " · "))
                    .font(.caption).foregroundStyle(.secondary)
                if let record = bet.hist?.text {
                    Text("Won \(record)").font(.caption2).foregroundStyle(Theme.green)
                }
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 3) {
                Text(Theme.percent(bet.prob)).font(.headline).foregroundStyle(Theme.chance(bet.prob))
                Text(bet.breakeven).font(.caption.monospacedDigit()).foregroundStyle(Theme.blue)
                Text(Theme.dollars(bet.paysOn10)).font(.caption2).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }
}

/// A parlay: its legs (each with its own chance), the parlay's chance and payout.
struct ParlayRow: View {
    let name: String
    let parlay: Parlay

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("\(name) · \(parlay.legs.count)-leg parlay").font(.subheadline.weight(.semibold))
                Spacer()
                Text(Theme.percent(parlay.prob)).font(.headline)
                    .foregroundStyle(parlay.prob >= 0.4 ? Theme.amber : Theme.red)
            }
            ForEach(parlay.legs) { leg in
                HStack(alignment: .firstTextBaseline) {
                    Text("•").foregroundStyle(.secondary)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(leg.label + (leg.league.map { " [\($0.uppercased())]" } ?? ""))
                            .font(.caption)
                        if let game = leg.game { Text(game).font(.caption2).foregroundStyle(.secondary) }
                    }
                    Spacer()
                    Text(Theme.percent(leg.prob)).font(.caption.weight(.semibold))
                        .foregroundStyle(Theme.chance(leg.prob))
                }
            }
            HStack {
                Text("Worst odds \(parlay.breakeven)").font(.caption).foregroundStyle(Theme.blue)
                Spacer()
                Text("$10 pays \(parlay.back == nil ? "" : "≈")\(Theme.dollars(parlay.paysOn10))")
                    .font(.caption.weight(.semibold))
            }
        }
        .padding(.vertical, 4)
    }
}

struct Badge: View {
    let text: String

    var body: some View {
        Text(text).font(.caption2.weight(.semibold))
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(Theme.amber.opacity(0.18), in: Capsule())
            .foregroundStyle(Theme.amber)
    }
}

/// Shown under every list of picks (App Store and FTC: no promises, 21+, help line).
struct Disclaimer: View {
    var body: some View {
        Section {
            Text("Chances are estimates from models and betting lines, not guarantees. Odds shown are break-even: bet only if FanDuel pays that or better. For entertainment; 21+. Gambling problem? Call 1-800-GAMBLER.")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .listRowBackground(Color.clear)
    }
}
