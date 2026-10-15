import SwiftUI

/// Every line FanDuel offers in a league, game lines and player props, priced against
/// every sportsbook: the true chance, FanDuel's price and its value, and the best price
/// anywhere. Best value first.
struct ValueView: View {
    @State private var league = "nfl"
    @State private var kind = "all"
    @State private var board: ValueBoard?
    @State private var error: String?
    @State private var showPaywall = false

    private let leagues = [("nfl", "NFL"), ("ncaaf", "College"), ("ufc", "UFC"), ("mlb", "MLB")]

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Picker("League", selection: $league) {
                        ForEach(leagues, id: \.0) { Text($0.1).tag($0.0) }
                    }
                    .pickerStyle(.segmented)
                    Picker("Show", selection: $kind) {
                        Text("Everything").tag("all")
                        Text("Player props").tag("props")
                        Text("Game lines").tag("lines")
                    }
                    .pickerStyle(.segmented)
                }
                .listRowBackground(Theme.card)
                if let board {
                    summary(board)
                    if board.locked {
                        LockedRow(text: "See every line's true chance and best price with a subscription") {
                            showPaywall = true
                        }
                        .listRowBackground(Theme.card)
                    } else {
                        Section("Best value at FanDuel first") {
                            ForEach(board.offers.filter { kind == "all" || $0.isProp == (kind == "props") }) {
                                OfferRow(offer: $0)
                            }
                        }
                        .listRowBackground(Theme.card)
                    }
                } else if let error {
                    Text(error).foregroundStyle(.secondary)
                } else {
                    ProgressView("Checking every sportsbook...")
                }
                Section {
                    Text("True chance: every sportsbook's price with its cut taken out, sharp books like Pinnacle counting most. Value: what $1 at FanDuel's price earns on average at that chance. \"Rough\" means only Yes prices were posted, so the cut is estimated. \"Check news\" means FanDuel is far from every other book: an injury or a stale line, not free money.")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                .listRowBackground(Color.clear)
                Disclaimer()
            }
            .scrollContentBackground(.hidden)
            .background(Theme.background)
            .navigationTitle("Value")
            .refreshable { await load() }
            .task { await load() }
            .onChange(of: league) { _ in Task { await load() } }
            .onChange(of: kind) { _ in Task { await load() } }
            .sheet(isPresented: $showPaywall) { PaywallView() }
        }
    }

    private func summary(_ board: ValueBoard) -> some View {
        Section {
            HStack {
                stat("\(board.summary.lines)", "lines checked")
                stat("\(board.summary.plusEv)", "with value", Theme.green)
                stat("\(board.summary.betterElsewhere)", "better elsewhere", Theme.blue)
            }
        }
        .listRowBackground(Theme.card)
    }

    private func stat(_ value: String, _ name: String, _ color: Color = .primary) -> some View {
        VStack {
            Text(value).font(.title3.weight(.bold)).foregroundStyle(color)
            Text(name).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }

    private func load() async {
        do { board = try await API.shared.value(league, kind: kind); error = nil }
        catch { board = nil; self.error = error.localizedDescription }
    }
}

struct OfferRow: View {
    let offer: LineOffer

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(offer.label).font(.subheadline.weight(.semibold))
                Spacer()
                Text(String(format: "%+.1f%%", offer.ev * 100)).font(.headline)
                    .foregroundStyle(offer.ev > 0 ? Theme.green : .secondary)
            }
            Text(offer.game).font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 10) {
                Text("True chance \(Theme.percent(offer.chance))").font(.caption)
                Text("FanDuel \(LineOffer.american(offer.fanduel))").font(.caption.weight(.semibold))
                if offer.best > offer.fanduel {
                    Text("Best \(LineOffer.american(offer.best)) at \(LineOffer.bookName(offer.bestBook))")
                        .font(.caption).foregroundStyle(Theme.blue)
                }
            }
            HStack(spacing: 6) {
                Text("\(offer.books) books\(offer.sharp ? " incl. sharp" : "")")
                    .font(.caption2).foregroundStyle(.secondary)
                if offer.rough { Badge(text: "Rough") }
                if offer.check { Badge(text: "Check news") }
            }
        }
        .padding(.vertical, 3)
    }
}
