import Foundation

@main
struct SubscriptionQuotaStateCheck {
    static func main() throws {
        // 断言的是中文输出，不能随跑测试那台机器的系统语言变化。
        L10n.forcedLanguage = .zh
        try expect(
            SubscriptionQuotaState.resolve([
                (value: nil, stale: nil),
                (value: nil, stale: true),
            ]) == .unavailable,
            "missing quota windows should be unavailable"
        )
        try expect(
            SubscriptionQuotaState.resolve([
                (value: 42, stale: false),
                (value: 73, stale: nil),
            ]) == .available,
            "fresh quota windows should be available"
        )
        try expect(
            SubscriptionQuotaState.resolve([
                (value: 42, stale: false),
                (value: 73, stale: true),
            ]) == .partiallyStale,
            "mixed freshness should be partially stale"
        )

        let expired = SubscriptionQuotaState.resolve([
            (value: 100, stale: true),
            (value: 73, stale: true),
        ])
        try expect(expired == .expired, "all stale quota windows should be expired")

        let now = Date(timeIntervalSince1970: 1_800_000_000)
        try expect(SubscriptionQuotaPresentation.hasResetSinceReading(
                       stale: true, reset: 1_800_000_000 - 60, now: now),
                   "a stale reading whose window already reset belongs to the previous window")
        try expect(!SubscriptionQuotaPresentation.hasResetSinceReading(
                       stale: true, reset: 1_800_000_000 + 60, now: now),
                   "a stale reading inside its window is still shown as the last known value")
        try expect(!SubscriptionQuotaPresentation.hasResetSinceReading(
                       stale: false, reset: 1_800_000_000 - 60, now: now),
                   "fresh readings are left alone")
        try expect(!SubscriptionQuotaPresentation.hasResetSinceReading(
                       stale: true, reset: nil, now: now),
                   "without a reset time there is nothing to compare")

        try expect(SubscriptionQuotaPresentation.remainingLabel(0) == "已用尽",
                   "zero remaining should use an explicit exhausted label")
        try expect(SubscriptionQuotaPresentation.remainingLabel(-4) == "已用尽",
                   "negative remaining should be clamped")
        try expect(SubscriptionQuotaPresentation.remainingLabel(42.4) == "42%",
                   "positive remaining should stay numeric")
        try expect(SubscriptionQuotaPresentation.remainingLabel(120) == "100%",
                   "remaining percentage should not exceed 100 percent")

        print("subscription quota state checks passed")
    }

    private static func expect(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        guard condition() else {
            throw NSError(domain: "SubscriptionQuotaStateCheck", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: message])
        }
    }
}
