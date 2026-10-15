import RevenueCat
import SwiftUI

/// Multi-Lays: parlays mixing leagues.
struct MultiView: View {
    @State private var multi: MultiLays?
    @State private var error: String?
    @State private var showPaywall = false

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("Parlays with legs from at least two leagues (NFL, college football, UFC, MLB), each paying at least $40 back on $10, likeliest first.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                .listRowBackground(Theme.card)
                if let multi {
                    Section {
                        ForEach(Array(multi.parlays.enumerated()), id: \.element.id) { i, p in
                            ParlayRow(name: "X\(i + 1)", parlay: p)
                        }
                        if multi.locked {
                            LockedRow(text: "\(multi.lockedCount ?? 0) Multi-Lays with a subscription") {
                                showPaywall = true
                            }
                        }
                    }
                    .listRowBackground(Theme.card)
                } else if let error {
                    Text(error).foregroundStyle(.secondary)
                } else {
                    ProgressView()
                }
                Disclaimer()
            }
            .scrollContentBackground(.hidden)
            .background(Theme.background)
            .navigationTitle("Multi-Lays")
            .refreshable { await load() }
            .task { await load() }
            .sheet(isPresented: $showPaywall) { PaywallView() }
        }
    }

    private func load() async {
        do { multi = try await API.shared.multi(); error = nil } catch { self.error = error.localizedDescription }
    }
}

/// The builder's graded record: open to everyone.
struct RecordView: View {
    @State private var record: Record?
    @State private var error: String?

    var body: some View {
        NavigationStack {
            List {
                if let record {
                    Section {
                        let settled = record.won + record.lost
                        HStack {
                            stat("Won", record.won, Theme.green)
                            stat("Lost", record.lost, Theme.red)
                            stat("Pending", record.pending, .secondary)
                        }
                        if settled > 0 {
                            Text("\(Theme.percent(Double(record.won) / Double(settled))) of settled picks won, counting only lines FanDuel always offers.")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                    .listRowBackground(Theme.card)
                    Section("Newest results") {
                        ForEach(record.recent) { e in
                            HStack(alignment: .top) {
                                Image(systemName: e.status == "won" ? "checkmark.circle.fill"
                                      : e.status == "lost" ? "xmark.circle.fill" : "minus.circle")
                                    .foregroundStyle(e.status == "won" ? Theme.green
                                                     : e.status == "lost" ? Theme.red : .secondary)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(e.kind == "single" ? (e.legs.first?.pick ?? "")
                                         : "\(e.legs.count)-leg parlay").font(.subheadline)
                                    Text(e.legs.compactMap(\.game).first ?? "").font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                Text(Theme.percent(e.prob)).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                    .listRowBackground(Theme.card)
                } else if let error {
                    Text(error).foregroundStyle(.secondary)
                } else {
                    ProgressView()
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.background)
            .navigationTitle("Record")
            .refreshable { await load() }
            .task { await load() }
        }
    }

    private func stat(_ name: String, _ value: Int, _ color: Color) -> some View {
        VStack {
            Text("\(value)").font(.title2.weight(.bold)).foregroundStyle(color)
            Text(name).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }

    private func load() async {
        do { record = try await API.shared.record(); error = nil } catch { self.error = error.localizedDescription }
    }
}

/// Subscription, restore, and the fine print.
struct SettingsView: View {
    @EnvironmentObject var subscription: Subscription
    @State private var showPaywall = false

    var body: some View {
        NavigationStack {
            List {
                Section("Subscription") {
                    if subscription.isSubscriber {
                        Label("Bet Builder Pro is active", systemImage: "checkmark.seal.fill")
                            .foregroundStyle(Theme.green)
                    } else {
                        Button("Get Bet Builder Pro") { showPaywall = true }
                    }
                    Button("Restore purchases") { Task { await subscription.restore() } }
                    if let message = subscription.message {
                        Text(message).font(.footnote).foregroundStyle(.secondary)
                    }
                }
                Section("About the picks") {
                    Text("Chances come from models tested on past seasons and from betting lines with the sportsbook's cut taken out. They are estimates, not guarantees, and past results don't promise future ones.")
                        .font(.footnote)
                    Text("Bet Builder doesn't take bets. Only bet where it's legal for you, and only what you can afford to lose. 21+. Gambling problem? Call 1-800-GAMBLER.")
                        .font(.footnote)
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.background)
            .navigationTitle("More")
            .sheet(isPresented: $showPaywall) { PaywallView() }
        }
    }
}

/// The subscription offer.
struct PaywallView: View {
    @EnvironmentObject var subscription: Subscription
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 16) {
                Text("Bet Builder Pro").font(.largeTitle.weight(.bold))
                VStack(alignment: .leading, spacing: 10) {
                    Label("Every pick for NFL, college football, UFC and MLB", systemImage: "list.number")
                    Label("Parlays, Money Makers and Multi-Lays", systemImage: "square.stack.3d.up")
                    Label("Each bet's chance, worst odds worth taking and payout", systemImage: "percent")
                    Label("Updated through the day as lines and injuries change", systemImage: "arrow.clockwise")
                }
                .font(.subheadline)
                Spacer()
                if !subscription.enabled {
                    Text("Subscriptions aren't open yet.").foregroundStyle(.secondary)
                } else if subscription.packages.isEmpty {
                    ProgressView()
                } else {
                    ForEach(subscription.packages, id: \.identifier) { package in
                        Button {
                            Task {
                                await subscription.buy(package)
                                if subscription.isSubscriber { dismiss() }
                            }
                        } label: {
                            HStack {
                                Text(package.storeProduct.localizedTitle)
                                Spacer()
                                Text(package.storeProduct.localizedPriceString)
                            }
                            .padding().frame(maxWidth: .infinity)
                            .background(Theme.blue, in: RoundedRectangle(cornerRadius: 12))
                            .foregroundStyle(.white)
                        }
                    }
                }
                Button("Restore purchases") { Task { await subscription.restore() } }
                    .frame(maxWidth: .infinity)
                if let message = subscription.message {
                    Text(message).font(.footnote).foregroundStyle(.secondary)
                }
                Text("Renews automatically until cancelled in your Apple ID settings. Picks are estimates, not guarantees. 21+.")
                    .font(.caption2).foregroundStyle(.secondary)
            }
            .padding()
            .background(Theme.background)
            .toolbar { Button("Close") { dismiss() } }
            .task { await subscription.refresh() }
        }
    }
}

/// Asked once, before anything else.
struct AgeGate: View {
    let confirm: () -> Void

    var body: some View {
        VStack(spacing: 20) {
            Spacer()
            Text("Bet Builder").font(.largeTitle.weight(.bold))
            Text("Betting picks and win chances for NFL, college football, UFC and MLB. You must be 21 or older to use this app.")
                .multilineTextAlignment(.center).foregroundStyle(.secondary)
            Button(action: confirm) {
                Text("I'm 21 or older").frame(maxWidth: .infinity).padding()
                    .background(Theme.blue, in: RoundedRectangle(cornerRadius: 12))
                    .foregroundStyle(.white)
            }
            Spacer()
            Text("Gambling problem? Call 1-800-GAMBLER.").font(.caption).foregroundStyle(.secondary)
        }
        .padding(24)
        .background(Theme.background)
    }
}
