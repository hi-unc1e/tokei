import Foundation

private enum TestFailure: Error {
    case assertion(String)
}

@main
struct QuotaHistoryStoreCheck {
    static func main() throws {
        // 断言的是中文输出，不能随跑测试那台机器的系统语言变化。
        L10n.forcedLanguage = .zh
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("tokei-quota-history-\(UUID().uuidString)")
        let fileURL = directory.appendingPathComponent("quota_history.json")
        defer { try? FileManager.default.removeItem(at: directory) }

        let base = Date(timeIntervalSince1970: 1_800_000_000)
        let store = QuotaHistoryStore(fileURL: fileURL)
        store.record(
            QuotaCapture(
                claudeFiveHourRemaining: 82,
                claudeWeekRemaining: 61,
                claudeFableWeekRemaining: 17,
                codexWeekRemaining: 49,
                claudeModelTotals: ["claude-opus": 100],
                codexModelTotals: ["gpt-5": 200]
            ),
            at: base
        )
        try expect(store.points.count == 1, "first capture should create one point")
        try expect(store.points[0].claudeActivity.isEmpty, "first capture should establish a baseline")

        store.record(
            QuotaCapture(
                claudeFiveHourRemaining: 80.5,
                claudeWeekRemaining: 60.5,
                claudeFableWeekRemaining: 16.5,
                codexWeekRemaining: 48.5,
                claudeModelTotals: ["claude-opus": 130],
                codexModelTotals: ["gpt-5": 225]
            ),
            at: base.addingTimeInterval(30)
        )
        try expect(store.points.count == 1, "captures in one minute should merge")
        try expect(store.points[0].claudeFiveHourRemaining == 80.5, "same-minute quota should use latest value")
        try expect(store.points[0].claudeFableWeekRemaining == 16.5, "Fable quota should use latest value")
        try expect(store.points[0].claudeActivity == [
            QuotaModelActivity(model: "claude-opus", tokenDelta: 30),
        ], "same-minute model delta should be recorded")

        store.record(
            QuotaCapture(
                claudeFiveHourRemaining: 79,
                claudeWeekRemaining: 60,
                claudeFableWeekRemaining: 16,
                codexWeekRemaining: 48,
                claudeModelTotals: ["claude-opus": 150, "claude-sonnet": 10],
                codexModelTotals: ["gpt-5": 240]
            ),
            at: base.addingTimeInterval(65)
        )
        try expect(store.points.count == 2, "next minute should append a point")
        try expect(store.points[1].claudeActivity.map(\.model) == [
            "claude-opus", "claude-sonnet",
        ], "new and growing models should both be attributed")

        store.record(
            QuotaCapture(
                claudeFiveHourRemaining: 78,
                claudeWeekRemaining: 59,
                codexWeekRemaining: 47
            ),
            at: base.addingTimeInterval(125)
        )
        store.record(
            QuotaCapture(
                claudeFiveHourRemaining: 77,
                claudeWeekRemaining: 58,
                codexWeekRemaining: 46,
                claudeModelTotals: ["claude-opus": 170, "claude-sonnet": 10],
                codexModelTotals: ["gpt-5": 260]
            ),
            at: base.addingTimeInterval(185)
        )
        try expect(store.points.last?.claudeActivity == [
            QuotaModelActivity(model: "claude-opus", tokenDelta: 20),
        ], "a transient empty scan should not reset the activity baseline")

        let reloaded = QuotaHistoryStore(fileURL: fileURL)
        try expect(reloaded.points == store.points, "history should survive a reload")

        reloaded.record(
            QuotaCapture(
                claudeFiveHourRemaining: 100,
                claudeWeekRemaining: 100,
                claudeFableWeekRemaining: 100,
                codexWeekRemaining: 100
            ),
            at: base.addingTimeInterval(8 * 24 * 60 * 60)
        )
        try expect(reloaded.points.count == 1, "points outside retention should be pruned")

        try checkProjection()
        try checkDailyConsumption()
        print("quota history store checks passed")
    }

    private static func checkProjection() throws {
        try expect(
            QuotaHistoryTool.claude.rawValue == "Claude Code",
            "Claude quota history should use the full product name"
        )
        let base = 1_800_000_000
        let points = [
            historyPoint(base, 80, 60, 20, activity: []),
            historyPoint(base + 60, 80, 60, 20, activity: []),
            historyPoint(base + 120, 80, 60, 20, activity: []),
            historyPoint(
                base + 180,
                79,
                59.5,
                19.5,
                activity: [
                    QuotaModelActivity(model: "Claude Opus", tokenDelta: 300),
                    QuotaModelActivity(model: "Claude Fable", tokenDelta: 100),
                ]
            ),
            historyPoint(
                base + 240,
                79,
                59.5,
                19.5,
                activity: [
                    QuotaModelActivity(model: "Claude Fable", tokenDelta: 200),
                    QuotaModelActivity(model: "Claude Opus", tokenDelta: 600),
                ]
            ),
            historyPoint(base + 300, 79, 59.5, 19.5, activity: []),
        ]
        let projection = QuotaHistoryProjection(points: points, tool: .claude)

        try expect(projection.latestValues["5 小时"] == 79, "projection should keep the latest value")
        try expect(projection.latestDatumIDs.count == 3, "each series should have one latest marker")
        try expect(projection.lineData.count == 12, "flat plateaus should compact to transition edges")
        try expect(projection.markerData.count == 6, "activity and latest markers should be preserved")
        try expect(projection.dropEvents.count == 3, "drops should be detected for every Claude window")
        try expect(projection.activityEvents.count == 2, "detailed model activity should remain visible")
        try expect(projection.hoverSamples.count == 6, "every sampled minute should remain hoverable")
        let nearest = projection.nearestHoverSample(
            to: Date(timeIntervalSince1970: TimeInterval(base + 250))
        )
        try expect(nearest?.timestamp.timeIntervalSince1970 == TimeInterval(base + 240),
                   "hover should find the nearest sampled minute")
        try expect(nearest?.rows.map(\.window) == ["5 小时", "周 · 全部", "周 · Fable"],
                   "hover should include every available quota window")
        try expect(nearest?.activity.map(\.model) == ["Claude Fable", "Claude Opus"],
                   "hover should retain activity for the selected minute")

        let fableMarker = projection.markerData.first {
            $0.window == "周 · Fable" && !$0.activity.isEmpty
        }
        try expect(
            fableMarker?.activity.map(\.model) == ["Claude Fable"],
            "Fable markers should only include Fable activity"
        )
    }

    /// 每天用了多少额度（issue #85）。
    private static func checkDailyConsumption() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let day = 1_800_000_000 - 1_800_000_000 % 86_400
        let hour = 3600
        func codex(_ offset: Int, _ remaining: Double) -> QuotaHistoryPoint {
            QuotaHistoryPoint(timestamp: day + offset, codexWeekRemaining: remaining)
        }
        let points = [
            // 第一天：从 00:30 采到 23:30，82 → 75，用掉 7 个点
            codex(hour / 2, 82), codex(10 * hour, 80), codex(23 * hour + hour / 2, 75),
            // 第二天：75 → 72，回满到 97，再用到 90；中间 90 → 91 的小回升不算回满也不抵扣
            codex(24 * hour + hour / 2, 75), codex(30 * hour, 72), codex(31 * hour, 97),
            codex(40 * hour, 90), codex(41 * hour, 91), codex(47 * hour + hour / 2, 88),
            // 第三天：只在下午有采样，前一晚到中午的下降都记在这天，标「约」
            codex(62 * hour, 85),
        ]
        let rows = QuotaHistoryProjection.dailyConsumption(
            from: points, tool: .codex, calendar: calendar)
        try expect(rows.count == 3, "one row per calendar day: \(rows.count)")
        try expect(rows[2].consumed == 7 && rows[2].refills == 0 && rows[2].isComplete,
                   "day one uses 7 points and is fully sampled")
        try expect(rows[1].consumed == 3 + 7 + 3, "day two: drops add up, rises never offset them")
        try expect(rows[1].refills == 1, "only the jump to 97 is a refill, not 90 -> 91")
        try expect(rows[1].isComplete, "day two is sampled at both ends")
        try expect(rows[0].consumed == 3 && !rows[0].isComplete,
                   "a drop across an overnight gap makes the receiving day approximate")

        // 夜里没用、读数过期不留快照：空档里额度没动，两天都说得准
        let quietNight = QuotaHistoryProjection.dailyConsumption(
            from: [codex(hour / 2, 60), codex(18 * hour, 55), codex(34 * hour, 55),
                   codex(40 * hour, 50)],
            tool: .codex, calendar: calendar)
        try expect(quietNight.count == 2 && quietNight.allSatisfy(\.isComplete),
                   "an overnight gap without any drop is not approximate")
        try expect(quietNight[0].consumed == 5 && quietNight[1].consumed == 5,
                   "each day keeps its own drops")

        // 最早那天之前没有记录，开头缺采样时说不准
        let partialStart = QuotaHistoryProjection.dailyConsumption(
            from: [codex(12 * hour, 60), codex(20 * hour, 55)], tool: .codex, calendar: calendar)
        try expect(partialStart.count == 1 && !partialStart[0].isComplete,
                   "the oldest day starting late is approximate")
    }

    private static func historyPoint(
        _ timestamp: Int,
        _ fiveHour: Double,
        _ week: Double,
        _ fable: Double,
        activity: [QuotaModelActivity]
    ) -> QuotaHistoryPoint {
        QuotaHistoryPoint(
            timestamp: timestamp,
            claudeFiveHourRemaining: fiveHour,
            claudeWeekRemaining: week,
            claudeFableWeekRemaining: fable,
            codexWeekRemaining: 50,
            claudeActivity: activity,
            codexActivity: []
        )
    }

    private static func expect(
        _ condition: @autoclosure () -> Bool,
        _ message: String
    ) throws {
        if !condition() {
            throw TestFailure.assertion(message)
        }
    }
}
