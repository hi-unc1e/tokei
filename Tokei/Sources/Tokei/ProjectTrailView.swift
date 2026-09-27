import SwiftUI
import AppKit
import TokeiUpdateSecurity

/// henry-hq 项目状态协议：项目根 STATUS.md + .hq/verify.json（由 usage.30s.py --projects 读取）。
struct TrailStatus: Codable {
    var state: String
    var theme: String
    var value: String
    var updated: String
    var summary: String
    var needs_you: [String]
    var blocked: [String]
    var next: [String]
    var answered: Int
    var verify_total: Int
    var verify_passed: Int
    var verify_failed: [String]
    var verify_at: String
    var gate_gave_up_24h: Int

    /// 需要 Henry 看一眼：有待判断/阻塞，或机器验收失败，或闸门放弃过。
    var needsAttention: Bool {
        !needs_you.isEmpty || !blocked.isEmpty || !verify_failed.isEmpty || gate_gave_up_24h > 0
    }
}

struct TrailProject: Codable, Identifiable {
    var path: String
    var name: String
    var last_active: String
    var sessions: Int
    var tokens: Int
    var cost_cny: Double? = nil
    var cost: Double
    var top_model: String
    var tools: [String]
    var ports: [Int]?
    var status: TrailStatus? = nil
    var id: String { path }
}

struct ProjectTrailView: View {
    @Binding var cached: [TrailProject]?
    @State private var loading = false
    @State private var query = ""
    @AppStorage("pinnedProjects") private var pinnedRaw = ""

    private var projects: [TrailProject] { cached ?? [] }

    private var pinned: Set<String> {
        Set(pinnedRaw.split(separator: "\n").map(String.init))
    }

    private func togglePin(_ path: String) {
        var s = pinned
        if s.contains(path) { s.remove(path) } else { s.insert(path) }
        pinnedRaw = s.sorted().joined(separator: "\n")
    }

    private var filtered: [TrailProject] {
        let q = query.lowercased()
        let list = q.isEmpty ? projects : projects.filter {
            $0.name.lowercased().contains(q) || $0.path.lowercased().contains(q)
        }
        return list.sorted { a, b in
            let ap = pinned.contains(a.path), bp = pinned.contains(b.path)
            if ap != bp { return ap }
            return a.last_active > b.last_active
        }
    }

    private enum Group: String, CaseIterable { case attention, pinned, today, week, earlier, dormant }

    private func group(for p: TrailProject) -> Group {
        if p.status?.needsAttention == true { return .attention }
        if pinned.contains(p.path) { return .pinned }
        let cal = Calendar.current
        let today = cal.startOfDay(for: Date())
        guard let d = Self.parseDate(p.last_active) else { return .dormant }
        let days = cal.dateComponents([.day], from: cal.startOfDay(for: d), to: today).day ?? 999
        if days == 0 { return .today }
        if days <= 7 { return .week }
        if days > 14 { return .dormant }
        return .earlier
    }

    private func groupLabel(_ g: Group) -> String {
        switch g {
        case .attention: return "待我判断"
        case .pinned: return "置顶"
        case .today: return "今天"
        case .week: return "本周"
        case .earlier: return "更早"
        case .dormant: return "沉睡"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 7) {
                searchBar
                Button { loadData() } label: {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: Theme.fontSize(10), weight: .medium))
                        .foregroundStyle(Theme.tTertiary)
                        .frame(width: 24, height: 24)
                        .background(Circle().fill(Color.primary.opacity(0.06)))
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .tip("刷新")
            }
            attentionSummary
            if loading || cached == nil {
                HStack { Spacer(); ProgressView().controlSize(.small); Spacer() }
                    .frame(height: 120)
            } else if filtered.isEmpty {
                HStack { Spacer(); Text("无匹配项目").font(.system(size: Theme.fontSize(11))).foregroundStyle(Theme.tTertiary); Spacer() }
                    .frame(height: 80)
            } else {
                let grouped = Dictionary(grouping: filtered, by: { group(for: $0) })
                ForEach(Group.allCases, id: \.self) { g in
                    if let items = grouped[g], !items.isEmpty {
                        sectionHeader(groupLabel(g), dormant: g == .dormant)
                        ForEach(items) { p in
                            projectRow(p)
                        }
                    }
                }
                footer
            }
        }
        .onAppear { if cached == nil { loadData() } }
    }

    var searchBar: some View {
        HStack(spacing: 7) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: Theme.fontSize(10), weight: .medium))
                .foregroundStyle(Theme.tTertiary)
            TextField("搜索项目…", text: $query)
                .font(.system(size: Theme.fontSize(11)))
                .textFieldStyle(.plain)
                .foregroundStyle(Theme.tPrimary)
            if !query.isEmpty {
                Button { query = "" } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: Theme.fontSize(10))).foregroundStyle(Theme.tTertiary)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 8, style: .continuous)
            .fill(Color.primary.opacity(0.06)))
    }

    func sectionHeader(_ title: String, dormant: Bool = false) -> some View {
        HStack(spacing: 5) {
            if dormant {
                Text("💤").font(.system(size: Theme.fontSize(10)))
            }
            if title == "待我判断" {
                Text("🧑‍⚖️").font(.system(size: Theme.fontSize(10)))
            }
            Text(title)
                .font(.system(size: Theme.fontSize(11), weight: .semibold))
                .foregroundStyle(dormant ? Theme.tTertiary : Theme.tSecondary)
        }
        .padding(.top, 4)
    }

    func projectRow(_ p: TrailProject) -> some View {
        HStack(spacing: 9) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 5) {
                    Image(systemName: "folder.fill")
                        .font(.system(size: Theme.fontSize(10), weight: .medium))
                        .foregroundStyle(Theme.claude.opacity(0.8))
                    Text(p.name)
                        .font(.system(size: Theme.fontSize(12), weight: .semibold))
                        .foregroundStyle(Theme.tPrimary)
                        .lineLimit(1)
                    ForEach(p.tools, id: \.self) { t in
                        Circle().fill(toolColor(t)).frame(width: 5, height: 5)
                    }
                }
                Text(abbreviatePath(p.path))
                    .font(.system(size: Theme.fontSize(9), design: .monospaced))
                    .foregroundStyle(Theme.tTertiary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                if let s = p.status {
                    statusBlock(s)
                }
                HStack(spacing: 6) {
                    Text(Fmt.relativeDate(p.last_active))
                        .font(.system(size: Theme.fontSize(9))).foregroundStyle(Theme.tTertiary)
                    Text("·").foregroundStyle(Theme.tTertiary).font(.system(size: Theme.fontSize(9)))
                    Text("\(p.sessions) sessions")
                        .font(.system(size: Theme.fontSize(9))).foregroundStyle(Theme.tTertiary)
                    Text("·").foregroundStyle(Theme.tTertiary).font(.system(size: Theme.fontSize(9)))
                    Text(nativeMoney(p.cost, p.cost_cny))
                        .font(.system(size: Theme.fontSize(9), weight: .medium)).foregroundStyle(Theme.tSecondary)
                    if !p.top_model.isEmpty {
                        Text(p.top_model)
                            .font(.system(size: Theme.fontSize(8), design: .monospaced))
                            .foregroundStyle(Theme.tTertiary)
                            .lineLimit(1)
                    }
                }
                if let ports = p.ports, !ports.isEmpty {
                    ScrollView(.horizontal, showsIndicators: ports.count > 4) {
                        HStack(spacing: 5) {
                            ForEach(ports, id: \.self) { port in
                                Button {
                                    if let url = URL(string: "http://localhost:\(port)") {
                                        NSWorkspace.shared.open(url)
                                    }
                                } label: {
                                    HStack(spacing: 3) {
                                        Circle().fill(.green).frame(width: 5, height: 5)
                                        Text("localhost:\(port)")
                                            .font(.system(size: Theme.fontSize(9), weight: .medium, design: .monospaced))
                                            .foregroundStyle(Theme.hermes)
                                            .lineLimit(1)
                                    }
                                    .fixedSize(horizontal: true, vertical: false)
                                    .padding(.horizontal, 6).padding(.vertical, 2)
                                    .background(Capsule().fill(Theme.hermes.opacity(0.12)))
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                    .frame(height: 22)
                }
            }
            Spacer(minLength: 4)
            Button { togglePin(p.path) } label: {
                Image(systemName: pinned.contains(p.path) ? "star.fill" : "star")
                    .font(.system(size: Theme.fontSize(10), weight: .medium))
                    .foregroundStyle(pinned.contains(p.path) ? Theme.qoder : Theme.tTertiary)
            }
            .buttonStyle(.plain)
            .tip(pinned.contains(p.path) ? "取消置顶" : "置顶")
        }
        .padding(.horizontal, 10).padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous)
            .fill(Color.primary.opacity(0.04)))
        .contentShape(Rectangle())
        .onTapGesture { openInITerm(p.path) }
        .contextMenu {
            if p.status != nil {
                Button("打开 STATUS.md") { openFile(p.path, "STATUS.md") }
                Button("打开 ACCEPTANCE.md") { openFile(p.path, "ACCEPTANCE.md") }
                Button("打开 DECISIONS.md") { openFile(p.path, "DECISIONS.md") }
                Divider()
            }
            Button("在终端打开") { openInTerminal(p.path) }
            Button("在 Ghostty 打开") { openInGhostty(p.path) }
            Button("在 iTerm 打开") { openInITerm(p.path) }
            Divider()
            Button("在 Finder 中显示") { NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: p.path) }
            Button("用 VS Code 打开") { openInVSCode(p.path) }
            Divider()
            Button("复制路径") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(p.path, forType: .string)
            }
        }
    }

    // MARK: - henry-hq 状态

    @ViewBuilder
    var attentionSummary: some View {
        let tracked = projects.compactMap(\.status)
        let needs = tracked.reduce(0) { $0 + $1.needs_you.count }
        let blocked = tracked.reduce(0) { $0 + $1.blocked.count }
        let failing = tracked.filter { !$0.verify_failed.isEmpty }.count
        if !tracked.isEmpty {
            HStack(spacing: 6) {
                statusChip("❓ \(needs) 待判断", needs > 0 ? Theme.qoder : Theme.tTertiary)
                statusChip("⛔ \(blocked) 阻塞", blocked > 0 ? Theme.zed : Theme.tTertiary)
                statusChip(failing > 0 ? "✗ \(failing) 验收失败" : "✓ 验收无失败",
                           failing > 0 ? Theme.zed : Theme.hermes)
                Spacer(minLength: 0)
            }
            .help("来自已接入 henry-hq 的 \(tracked.count) 个项目（STATUS.md / .hq/verify.json）")
        }
    }

    func statusBlock(_ s: TrailStatus) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                if !s.theme.isEmpty {
                    statusChip(s.value.isEmpty ? s.theme : "\(s.theme) · \(s.value)", Theme.tSecondary)
                }
                if s.state != "active" {
                    statusChip(s.state == "parked" ? "搁置" : s.state, Theme.tTertiary)
                }
                if !s.needs_you.isEmpty { statusChip("❓\(s.needs_you.count)", Theme.qoder) }
                if !s.blocked.isEmpty { statusChip("⛔\(s.blocked.count)", Theme.zed) }
                if s.verify_total > 0 {
                    let ok = s.verify_failed.isEmpty
                    statusChip("\(ok ? "✓" : "✗") \(s.verify_passed)/\(s.verify_total)", ok ? Theme.hermes : Theme.zed)
                        .help(ok ? "机器验收全部通过 · \(s.verify_at)" : "未通过：\(s.verify_failed.joined(separator: ", "))")
                }
                if s.answered > 0 { statusChip("已答复 \(s.answered)", Theme.tTertiary) }
                if s.gate_gave_up_24h > 0 {
                    statusChip("闸门放弃 \(s.gate_gave_up_24h)", Theme.zed)
                        .help("近 24 小时 agent 两次没能通过验收闸门，被迫放行")
                }
            }
            if !s.summary.isEmpty {
                Text(s.summary)
                    .font(.system(size: Theme.fontSize(10)))
                    .foregroundStyle(Theme.tSecondary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(Array((s.blocked.map { "⛔ " + $0 } + s.needs_you.map { "❓ " + $0 }).prefix(3).enumerated()), id: \.offset) { _, line in
                Text(line)
                    .font(.system(size: Theme.fontSize(9.5)))
                    .foregroundStyle(Theme.tTertiary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
        }
        .padding(.top, 2)
    }

    func statusChip(_ text: String, _ color: Color) -> some View {
        Text(text)
            .font(.system(size: Theme.fontSize(9), weight: .semibold))
            .foregroundStyle(color)
            .lineLimit(1)
            .fixedSize()
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(Capsule().fill(color.opacity(0.14)))
    }

    func openFile(_ dir: String, _ name: String) {
        NSWorkspace.shared.open(URL(fileURLWithPath: dir).appendingPathComponent(name))
    }

    var footer: some View {
        let earliest = projects.compactMap({ Self.parseDate($0.last_active) }).min()
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd"
        let earliestStr = earliest.map { fmt.string(from: $0) } ?? "?"
        return Text("共 \(projects.count) 个项目 · 最远 \(earliestStr)")
            .font(.system(size: Theme.fontSize(9))).foregroundStyle(Theme.tTertiary)
            .frame(maxWidth: .infinity)
            .padding(.top, 4)
    }

    // MARK: - Helpers

    func toolColor(_ t: String) -> Color {
        switch t {
        case "claude": return Theme.claude
        case "codex": return Theme.codex
        case "grok": return Theme.grok
        case "hermes": return Theme.hermes
        case "zcode": return Theme.zcode
        case "mimocode": return Theme.mimocode
        case "pi": return Theme.pi
        case "prime_agent": return Theme.primeAgent
        case "workbuddy": return Theme.workbuddy
        case "workbuddy_ai": return Theme.workbuddyAI
        case "codebuddy": return Theme.codebuddy
        case "deepseek_harness": return Theme.deepseekHarness
        default: return Theme.tTertiary
        }
    }

    func abbreviatePath(_ path: String) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        if path.hasPrefix(home) { return "~" + path.dropFirst(home.count) }
        return path
    }

    func openInTerminal(_ path: String) {
        let command = "cd -- \(ShellEscaping.singleQuoted(path))"
        runAppleScript(
            """
            on run argv
                tell application "Terminal"
                    do script (item 1 of argv)
                    activate
                end tell
            end run
            """,
            argument: command
        )
    }

    func openInGhostty(_ path: String) {
        let escaped = ShellEscaping.singleQuoted(path)
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = ["ghostty", "-e", "/bin/zsh", "-c", "cd -- \(escaped) && exec zsh"]
        try? proc.run()
    }

    func openInITerm(_ path: String) {
        let shellCommand = "cd -- \(ShellEscaping.singleQuoted(path)) && exec /bin/zsh"
        let launchCommand = "/bin/zsh -c \(ShellEscaping.singleQuoted(shellCommand))"
        runAppleScript(
            """
            on run argv
                tell application "iTerm"
                    create window with default profile command (item 1 of argv)
                    activate
                end tell
            end run
            """,
            argument: launchCommand
        )
    }

    private func runAppleScript(_ source: String, argument: String) {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        proc.arguments = ["-e", source, argument]
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError = FileHandle.nullDevice
        try? proc.run()
    }

    func openInVSCode(_ path: String) {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = ["code", path]
        try? proc.run()
    }

    static func parseDate(_ s: String) -> Date? {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"
        return f.date(from: s)
    }

    func loadData() {
        loading = true
        DispatchQueue.global(qos: .utility).async {
            let data = DashboardView.runScript(["--projects"])
            let list = (try? JSONDecoder().decode([TrailProject].self, from: data)) ?? []
            DispatchQueue.main.async {
                cached = list
                loading = false
            }
        }
    }
}
