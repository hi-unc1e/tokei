import Foundation

/// 空用量卡片的措辞。
///
/// 一张「今日暂无用量」的卡片本身分不清三件事：这个区间真的没有用量、
/// 数据还没刷出来、或者统计坏了。三种情况长得一模一样，而只有第一种是结论。
/// 所以刷新中要说自己在刷；刷完了还是空，就拿最近一个有用量的区间作证。
enum UsageEmptyState: Equatable {
    case refreshing
    case empty(recent: String?)

    /// - Parameters:
    ///   - selected: 当前选中的区间。
    ///   - refreshing: 是否正在刷新。
    ///   - tokens: 某区间的 token 总量。
    static func resolve(
        selected: RangeKey,
        refreshing: Bool,
        tokens: (RangeKey) -> Int
    ) -> Self {
        if refreshing { return .refreshing }
        // 只在「今日」给旁证：其它区间为空通常不会让人怀疑统计坏了，
        // 而「今日」天天从零开始，最容易被误读成漏统计。
        guard selected == .today, tokens(.today) == 0 else { return .empty(recent: nil) }
        for key in [RangeKey.yesterday, .week, .month] where tokens(key) > 0 {
            return .empty(recent: "\(key.label) \(Fmt.human(tokens(key)))")
        }
        return .empty(recent: nil)
    }
}
