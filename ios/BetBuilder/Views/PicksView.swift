import SwiftUI

/// The board for one league: pick the league, the week (or card, or day) and the bet
/// type; then singles, parlays and money makers, likeliest first.
struct PicksView: View {
    @EnvironmentObject var subscription: Subscription
    @State private var leagues: [League] = []
    @State private var league = "nfl"
    @State private var week: Int?
    @State private var type = "All bets"
    @State private var section = UserDefaults.standard.integer(forKey: "startSection")  // "-startSection 1"
    @State private var board: Board?
    @State private var error: String?
    @State private var loading = false
    @State private var showPaywall = false

    private var current: League? { leagues.first { $0.id == league } }

    var body: some View {
        NavigationStack {
            List {
                controls
                if let board {
                    notes(board)
                    switch section {
                    case 0: singles(board)
                    case 1: parlays(board.parlays, locked: board.lockedCounts?.parlays, money: false)
                    default: parlays(board.money, locked: board.lockedCounts?.money, money: true)
                    }
                } else if let error {
                    Text(error).foregroundStyle(.secondary)
                } else {
                    ProgressView("Loading bets...")
                }
                Disclaimer()
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Theme.background)
            .navigationTitle(board?.label ?? "Bet Builder")
            .navigationBarTitleDisplayMode(.inline)
            .refreshable { await load() }
            .task { await start() }
            .sheet(isPresented: $showPaywall) { PaywallView() }
        }
    }

    // MARK: menus

    private var controls: some View {
        Section {
            Picker("League", selection: $league) {
                ForEach(leagues) { Text($0.id == "ncaaf" ? "College" : $0.name).tag($0.id) }
            }
            .pickerStyle(.segmented)
            .onChange(of: league) { _ in
                week = current?.weeks.first?.week
                if !(current?.types.contains(type) ?? false) { type = "All bets" }
                Task { await load() }
            }
            if let weeks = current?.weeks, !weeks.isEmpty {
                Picker("Games", selection: $week) {
                    ForEach(weeks, id: \.week) { Text($0.label).tag(Optional($0.week)) }
                }
                .onChange(of: week) { _ in Task { await load() } }
            }
            if let types = current?.types, types.count > 1 {
                Picker("Bet type", selection: $type) {
                    ForEach(types, id: \.self) { Text($0).tag($0) }
                }
                .onChange(of: type) { _ in Task { await load() } }
            }
            Picker("Show", selection: $section) {
                Text("Singles").tag(0)
                Text("Parlays").tag(1)
                Text("Money Maker").tag(2)
            }
            .pickerStyle(.segmented)
        }
        .listRowBackground(Theme.card)
    }

    @ViewBuilder
    private func notes(_ board: Board) -> some View {
        let starters = (board.starters ?? "").isEmpty ? nil : "Starters: \(board.starters ?? "")"
        let lines = [board.modelNote, board.note, starters].compactMap { $0 }.filter { !$0.isEmpty }
        if !lines.isEmpty {
            Section {
                ForEach(lines, id: \.self) {
                    Text($0).font(.footnote).foregroundStyle(.secondary)
                }
            }
            .listRowBackground(Theme.card)
        }
    }

    // MARK: bets

    @ViewBuilder
    private func singles(_ board: Board) -> some View {
        Section("Single bets · likeliest first") {
            ForEach(Array(board.singles.enumerated()), id: \.element.id) { i, bet in
                BetRow(number: i + 1, bet: bet)
            }
            if let more = board.lockedCounts?.singles, more > 0 {
                LockedRow(text: "\(more) more single bets with a subscription") { showPaywall = true }
            }
            if board.singles.isEmpty && board.lockedCounts == nil {
                Text("No bets here yet. Try another week or bet type.").foregroundStyle(.secondary)
            }
        }
        .listRowBackground(Theme.card)
    }

    @ViewBuilder
    private func parlays(_ list: [Parlay], locked: Int?, money: Bool) -> some View {
        Section(money ? "Money Maker · pays back at least double" : "Parlays · likeliest first") {
            ForEach(Array(list.enumerated()), id: \.element.id) { i, p in
                ParlayRow(name: "\(money ? "M" : "P")\(i + 1)", parlay: p)
            }
            if let locked, locked > 0 {
                LockedRow(text: "\(locked) \(money ? "money makers" : "parlays") with a subscription") {
                    showPaywall = true
                }
            } else if list.isEmpty {
                Text("None for this week.").foregroundStyle(.secondary)
            }
        }
        .listRowBackground(Theme.card)
    }

    // MARK: loading

    private func start() async {
        guard leagues.isEmpty else { return }
        do {
            leagues = try await API.shared.leagues().leagues
            if let first = leagues.first(where: { !$0.weeks.isEmpty }) {
                league = first.id
                week = first.weeks.first?.week
            }
        } catch {
            self.error = error.localizedDescription
        }
        await load()
    }

    private func load() async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        do {
            board = try await API.shared.board(league, week: week, type: type)
            error = nil
        } catch {
            board = nil
            self.error = error.localizedDescription
        }
    }
}

struct LockedRow: View {
    let text: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Label(text, systemImage: "lock.fill").font(.subheadline.weight(.semibold))
        }
    }
}
