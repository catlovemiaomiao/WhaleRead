// Called by the signed application entry before constructing a Qt application.
// Keeping the real application executable preserves its Sandbox identity and
// the user-selected-file entitlement required by the macOS Powerbox.
import AppKit
import Darwin
import Foundation
import StoreKit

enum PurchasePolicy {
    static func accepts(verified: Bool, transactionBundleID: String,
                        actualBundleID: String, expectedBundleID: String) -> Bool {
        verified && !expectedBundleID.isEmpty && actualBundleID == expectedBundleID
            && transactionBundleID == expectedBundleID
    }
}

struct StoreLauncher {
    static var actualBundleID: String { Bundle.main.bundleIdentifier ?? "" }

    static func policyStatus() -> Int32 {
        let id = ReleaseIdentity.bundleID
        let cases: [(Bool, String, String, Bool)] = [
            (false, id, id, false), (true, "other.application", id, false),
            (true, id, "other.application", false), (true, "", id, false),
            (true, id, id, true),
        ]
        let passed = cases.allSatisfy { verified, transactionID, actualID, expected in
            PurchasePolicy.accepts(verified: verified, transactionBundleID: transactionID,
                actualBundleID: actualID, expectedBundleID: id) == expected
        }
        return (passed ? 1 : 0) | (actualBundleID == id ? 2 : 0)
    }

    @MainActor
    static func retryAlert() -> Bool {
        let chinese = Locale.preferredLanguages.first?.hasPrefix("zh") ?? false
        NSApplication.shared.setActivationPolicy(.regular)
        NSApplication.shared.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = chinese ? "暂时无法验证鲸读的购买记录" : "Unable to verify your WhaleRead purchase"
        alert.informativeText = chinese
            ? "请确认已连接网络，并使用购买此应用的 Apple 账户登录 App Store。点击重试可向 App Store 刷新购买记录。你的书库和笔记不会被删除。"
            : "Connect to the internet and sign in to the App Store with the Apple Account used to purchase this app. Retry refreshes the purchase record with the App Store. Your library and notes are preserved."
        alert.addButton(withTitle: chinese ? "重试" : "Retry")
        alert.addButton(withTitle: chinese ? "退出" : "Quit")
        return alert.runModal() == .alertFirstButtonReturn
    }

    @MainActor
    static func validatePurchase() async -> Bool {
        guard actualBundleID == ReleaseIdentity.bundleID else { return false }
        var refresh = false
        while true {
            do {
                let result = try await (refresh ? AppTransaction.refresh() : AppTransaction.shared)
                if case .verified(let transaction) = result,
                   PurchasePolicy.accepts(verified: true, transactionBundleID: transaction.bundleID,
                       actualBundleID: actualBundleID, expectedBundleID: ReleaseIdentity.bundleID) {
                    return true
                }
            } catch {
                // Do not persist transaction payloads, Apple account data or errors.
            }
            if !retryAlert() { return false }
            refresh = true // A refresh happens only after an explicit Retry.
        }
    }

}

@MainActor
final class GateState { var result: Int32? }

@_cdecl("WhaleReadPurchasePolicyStatus")
public func purchasePolicyStatus() -> Int32 {
    StoreLauncher.policyStatus()
}

@_cdecl("WhaleReadVerifyPurchase")
public func verifyPurchase() -> Int32 {
    guard Thread.isMainThread else { return 0 }
    return MainActor.assumeIsolated {
        #if WHALEREAD_QA
        return ReleaseIdentity.mode == "qa"
            && StoreLauncher.actualBundleID == ReleaseIdentity.bundleID
            && StoreLauncher.actualBundleID.hasPrefix("local.sindy.jingdu.storeqa") ? 1 : 0
        #else
        let state = GateState()
        Task { @MainActor in
            state.result = await StoreLauncher.validatePurchase() ? 1 : 0
        }
        while state.result == nil {
            _ = RunLoop.main.run(mode: .default, before: Date().addingTimeInterval(0.05))
        }
        return state.result ?? 0
        #endif
    }
}
