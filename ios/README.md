# Bet Builder for iPhone

A SwiftUI app (iOS 16+) showing the Bet Builder's picks from the server in
`server/`, with a subscription (App Store, through RevenueCat) that unlocks every
pick. Free users see the top 3 singles of each board and the builder's record.

- `project.yml`: the Xcode project, generated with [XcodeGen](https://github.com/yonaskolb/XcodeGen)
  (`brew install xcodegen && xcodegen generate` in this folder, then open
  `BetBuilder.xcodeproj`).
- `BetBuilder/Demo/`: sample server answers. With no server address (or when launched
  with `-demo`) the app shows these, for previews and screenshots.
- `.github/workflows/ios.yml` builds it on a Mac and saves simulator screenshots.

Settings filled in at build time (`project.yml`, or `xcodebuild ... BET_API_URL=...`):
`BET_API_URL` (the server, e.g. `https://api.example.com`) and `REVENUECAT_API_KEY`
(RevenueCat's public Apple key). The RevenueCat entitlement is `pro`.

Still to do before the App Store: the server hosted, licensed data, the Apple
Developer account (under the business), the subscription product in App Store
Connect and RevenueCat, TestFlight uploads from the workflow, the privacy policy and
terms pages, and App Store screenshots and listing.
