import Foundation
import RevenueCat

/// The subscription, through RevenueCat (App Store purchases). Without an API key
/// (demo builds) purchases are off and the app shows what the server allows.
@MainActor
final class Subscription: ObservableObject {
    static let entitlement = "pro"

    @Published private(set) var isSubscriber = false
    @Published private(set) var packages: [Package] = []
    @Published var message: String?

    let enabled: Bool

    init() {
        let key = (Bundle.main.object(forInfoDictionaryKey: "RevenueCatAPIKey") as? String) ?? ""
        enabled = !key.isEmpty
        if enabled {
            Purchases.configure(withAPIKey: key)
            API.shared.userID = Purchases.shared.appUserID
        }
    }

    func refresh() async {
        guard enabled else { return }
        do {
            let info = try await Purchases.shared.customerInfo()
            isSubscriber = info.entitlements[Self.entitlement]?.isActive == true
            packages = try await Purchases.shared.offerings().current?.availablePackages ?? []
        } catch {
            message = "Couldn't check your subscription right now."
        }
    }

    func buy(_ package: Package) async {
        do {
            let result = try await Purchases.shared.purchase(package: package)
            if !result.userCancelled {
                isSubscriber = result.customerInfo.entitlements[Self.entitlement]?.isActive == true
            }
        } catch {
            message = "The purchase didn't go through: \(error.localizedDescription)"
        }
    }

    func restore() async {
        guard enabled else { return }
        do {
            let info = try await Purchases.shared.restorePurchases()
            isSubscriber = info.entitlements[Self.entitlement]?.isActive == true
            message = isSubscriber ? "Your subscription is restored." : "No subscription found."
        } catch {
            message = "Couldn't restore purchases: \(error.localizedDescription)"
        }
    }
}
