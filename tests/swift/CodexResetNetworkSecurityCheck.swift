import Foundation

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw NSError(domain: message, code: 1) }
}

/// Every URL is intercepted, including forbidden destinations. This harness never uses the network.
private final class FeedFixture: URLProtocol {
    private static let lock = NSLock()
    private static var handler: ((FeedFixture) -> Void)?
    private static var recorded: [URLRequest] = []
    private static var bytes = 0
    private static var stops = 0
    private static var eof = false
    private let stateLock = NSLock()
    private var stopped = false

    static func reset(_ handler: @escaping (FeedFixture) -> Void) {
        lock.lock(); defer { lock.unlock() }
        self.handler = handler
        recorded = []; bytes = 0; stops = 0; eof = false
    }

    static var observations: (requests: [URLRequest], bytes: Int, stops: Int, eof: Bool) {
        lock.lock(); defer { lock.unlock() }
        return (recorded, bytes, stops, eof)
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.lock.lock()
        Self.recorded.append(request)
        let handler = Self.handler
        Self.lock.unlock()
        guard let handler else { fatalError("request outside synthetic scenario") }
        handler(self)
    }
    override func stopLoading() {
        stateLock.lock(); stopped = true; stateLock.unlock()
        Self.lock.lock(); Self.stops += 1; Self.lock.unlock()
    }
    private var active: Bool {
        stateLock.lock(); defer { stateLock.unlock() }
        return !stopped
    }
    func response(status: Int = 200, type: String = "application/json", length: Int? = nil) {
        var headers = ["Content-Type": type]
        if let length { headers["Content-Length"] = String(length) }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status,
                            httpVersion: nil, headerFields: headers)!, cacheStoragePolicy: .notAllowed)
    }
    func send(_ data: Data, finish: Bool = true) {
        guard active else { return }
        Self.lock.lock(); Self.bytes += data.count; Self.lock.unlock()
        client?.urlProtocol(self, didLoad: data)
        if finish {
            Self.lock.lock(); Self.eof = true; Self.lock.unlock()
            client?.urlProtocolDidFinishLoading(self)
        }
    }
    func stream(_ chunk: Data, remaining: Int) {
        guard active, remaining > 0 else { return }
        send(chunk, finish: false)
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.03) { [self] in
            stream(chunk, remaining: remaining - 1)
        }
    }
    func redirect(to url: URL) {
        let response = HTTPURLResponse(url: request.url!, statusCode: 302, httpVersion: nil,
                                       headerFields: ["Location": url.absoluteString])!
        var redirected = URLRequest(url: url)
        // Synthetic headers/body ensure allowed hops are rebuilt as anonymous GETs.
        redirected.setValue("synthetic-only", forHTTPHeaderField: "Authorization")
        redirected.setValue("synthetic-only", forHTTPHeaderField: "Cookie")
        redirected.httpMethod = "POST"
        redirected.httpBody = Data("synthetic-only".utf8)
        client?.urlProtocol(self, wasRedirectedTo: redirected, redirectResponse: response)
    }
}

@main
struct CodexResetNetworkSecurityCheck {
    static func wait(_ condition: () -> Bool) throws {
        let deadline = Date().addingTimeInterval(4)
        while !condition() && Date() < deadline {
            RunLoop.main.run(until: Date().addingTimeInterval(0.01))
        }
        try expect(condition(), "timed out waiting for early cancellation/response")
    }

    static func main() throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [FeedFixture.self]
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        let root = URL(fileURLWithPath: CommandLine.arguments[1])
            .appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = root.appendingPathComponent("cache.json")
        let monitor = CodexResetMonitor(session: session, cacheURL: cache)
        let valid = Data("{\"schemaVersion\":1,\"checkedAt\":\"2026-10-15T06:00:00Z\",\"current\":null,\"lastLanded\":null}".utf8)
        var now = Date()
        func refresh() { now.addTimeInterval(601); monitor.refresh(enabled: true, now: now) }
        func assertAnonymous() throws {
            for request in FeedFixture.observations.requests {
                try expect(request.value(forHTTPHeaderField: "Authorization") == nil &&
                           request.value(forHTTPHeaderField: "Cookie") == nil && request.httpBody == nil &&
                           request.httpMethod == "GET", "all hops must be anonymous GETs")
            }
        }
        // Seed a genuine accepted/cacheable response before attempting rejected responses.
        FeedFixture.reset { p in p.response(length: valid.count); p.send(valid) }
        refresh(); try wait { !monitor.isRefreshing }
        try expect(!monitor.lastFetchFailed && monitor.snapshot != nil, "valid response accepted")
        let cachedBytes = try Data(contentsOf: cache)
        let fetchedAt = monitor.snapshot!.fetchedAt
        func assertPreserved() throws {
            try expect(monitor.lastFailure == .invalidResponse, "failure classified as invalid response")
            try expect(monitor.snapshot?.fetchedAt == fetchedAt, "last good snapshot unchanged")
            try expect((try? Data(contentsOf: cache)) == cachedBytes, "last good disk cache unchanged")
        }

        if CommandLine.arguments[2] == "sizes" {
            for declaredLength in [1_000_001, Int.max] {
                FeedFixture.reset { p in p.response(length: declaredLength) } // no body or EOF
                refresh(); try wait { !monitor.isRefreshing && FeedFixture.observations.stops > 0 }
                try expect(FeedFixture.observations.bytes == 0 && !FeedFixture.observations.eof,
                           "reject oversized advertised length before any body/EOF")
                try assertPreserved()
            }
            // Unknown and dishonest lengths both require counting actual decoded bytes.
            for length in [nil, 16] as [Int?] {
                FeedFixture.reset { p in
                    p.response(length: length)
                    p.stream(Data(repeating: 32, count: 250_000), remaining: 12)
                }
                refresh(); try wait { !monitor.isRefreshing && FeedFixture.observations.stops > 0 }
                let seen = FeedFixture.observations
                try expect(!seen.eof && seen.bytes < 3_000_000,
                           "oversized stream must stop before EOF/full body")
                try assertPreserved()
                RunLoop.main.run(until: Date().addingTimeInterval(0.05))
                try expect(monitor.lastFailure == .invalidResponse, "late cancellation cannot replace failure")
            }
            for size in [999_999, 1_000_000] {
                var padded = valid
                padded.append(Data(repeating: 32, count: size - valid.count))
                FeedFixture.reset { p in p.response(length: size); p.send(padded) }
                refresh(); try wait { !monitor.isRefreshing }
                try expect(!monitor.lastFetchFailed, "responses through exact byte limit accepted")
            }
            FeedFixture.reset { p in p.response(); p.stream(Data(repeating: 32, count: 10), remaining: 100) }
            refresh(); try wait { FeedFixture.observations.bytes > 0 }
            monitor.refresh(enabled: false)
            try wait { FeedFixture.observations.stops > 0 }
            RunLoop.main.run(until: Date().addingTimeInterval(0.05))
            try expect(!monitor.isRefreshing && !monitor.lastFetchFailed, "disable cancels stream without failure")
        } else {
            let forbidden = ["https://fixture.invalid/private-effect", "http://aihot.news/plain",
                             "https://aihot.news:444/wrong-port", "https://aihot.news.fixture.invalid/lookalike",
                             "https://user@aihot.news/userinfo", "file:///synthetic-only"]
            for target in forbidden {
                FeedFixture.reset { p in
                    if FeedFixture.observations.requests.count == 1 { p.redirect(to: URL(string: target)!) }
                    else { p.response(); p.send(valid) }
                }
                refresh(); try wait { !monitor.isRefreshing }
                try expect(FeedFixture.observations.requests.count == 1,
                           "forbidden redirect must not issue a target request: " + target)
                try assertPreserved()
            }
            for target in ["https://aihot.news/synthetic-final", "https://AIHOT.NEWS:443/synthetic-final"] {
                FeedFixture.reset { p in
                    if p.request.url?.path != "/synthetic-final" { p.redirect(to: URL(string: target)!) }
                    else { p.response(length: valid.count); p.send(valid) }
                }
                refresh(); try wait { !monitor.isRefreshing }
                try expect(FeedFixture.observations.requests.count == 2 && !monitor.lastFetchFailed,
                           "same HTTPS origin including explicit default port is allowed")
                try assertAnonymous()
            }
            FeedFixture.reset { p in
                p.redirect(to: URL(string: "https://aihot.news/loop")!)
            }
            refresh(); try wait { !monitor.isRefreshing }
            try expect(FeedFixture.observations.requests.count == 6 && monitor.lastFailure == .invalidResponse,
                       "same-origin redirect loop is bounded")
        }
        try assertAnonymous()
        print("reset network security " + CommandLine.arguments[2] + " checks passed")
    }
}
