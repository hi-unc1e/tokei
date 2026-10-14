import Foundation

enum SubscriptionQuotaState: Equatable {
    case unavailable
    case available
    case partiallyStale
    case expired

    static func resolve(_ windows: [(value: Double?, stale: Bool?)]) -> Self {
        let availableWindows = windows.filter { $0.value != nil }
        guard !availableWindows.isEmpty else { return .unavailable }

        let staleCount = availableWindows.filter { $0.stale == true }.count
        if staleCount == 0 { return .available }
        if staleCount == availableWindows.count { return .expired }
        return .partiallyStale
    }
}

/// 读数过期时照常展示最后一次读到的额度（变暗），不把整张卡换成警告框：
/// 应用退出一晚、工具久没用，读数自然会过期，这不是故障。
enum SubscriptionQuotaPresentation {
    /// 读数过期，而且窗口的重置时刻已经过了：上次的百分比属于上一个窗口，不能再当剩余显示。
    /// 也不能按回满显示——claude.ai 网页、别的设备都在用同一份额度，谎报满额比承认
    /// 不知道危险得多（issue #63）。界面只说「已重置」，等下一次读数。
    static func hasResetSinceReading(stale: Bool, reset: Int?, now: Date = Date()) -> Bool {
        guard stale, let reset else { return false }
        return TimeInterval(reset) <= now.timeIntervalSince1970
    }

    static func remainingLabel(_ remaining: Double) -> String {
        let clamped = min(100, max(0, remaining))
        return clamped < 0.5 ? L("已用尽") : String(format: "%.0f%%", clamped)
    }
}
