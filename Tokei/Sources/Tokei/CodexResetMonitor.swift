import Foundation
import Combine

final class CodexResetMonitor: ObservableObject {
    static let shared = CodexResetMonitor()
    static let enabledKey = "codexResetAnnouncementsEnabled"
    static let pageURL = URL(string: "https://aihot.news/codex-reset")!
    static let feedURL = URL(string: "https://aihot.news/api/site/codex-reset")!
    static let refreshInterval: TimeInterval = 10 * 60
    static let responseByteLimit = 1_000_000

    static func isEnabled(in defaults: UserDefaults = .standard) -> Bool {
        defaults.object(forKey: enabledKey) as? Bool ?? true
    }

    enum Failure: Error, Equatable {
        case network(Int), http(Int), invalidResponse, invalidData
    }

    struct Snapshot: Codable {
        let feed: CodexResetFeed
        let fetchedAt: Date
    }

    @Published private(set) var snapshot: Snapshot?
    @Published private(set) var isRefreshing = false
    @Published private(set) var lastFetchFailed = false
    @Published private(set) var lastFailure: Failure?
    private var failureCount = 0
    private var retryInterval: TimeInterval {
        min(Self.refreshInterval, 60 * pow(2, Double(min(failureCount - 1, 4))))
    }
    private var lastAttempt = Date.distantPast
    private var requestTask: CodexResetFeedRequest?
    private var requestID: UUID?
    private let sessionConfiguration: URLSessionConfiguration
    private let cacheURL: URL?

    init(session: URLSession? = nil, cacheURL: URL? = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".tokei/codex_reset_announcements.json")) {
        // Copy only the injected configuration (e.g. offline URLProtocol fixtures).
        // The request owns its security delegate; an injected session cannot bypass it.
        let config = session?.configuration ?? URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil
        config.httpShouldSetCookies = false
        config.urlCredentialStorage = nil
        config.urlCache = nil
        config.timeoutIntervalForRequest = 15
        config.timeoutIntervalForResource = 20
        self.sessionConfiguration = config
        self.cacheURL = cacheURL
        if let cacheURL, let data = try? Data(contentsOf: cacheURL), data.count <= 1_000_000,
           let cached = try? JSONDecoder().decode(Snapshot.self, from: data),
           (try? cached.feed.validate()) != nil {
            snapshot = cached
        }
    }

    func refresh(enabled: Bool = CodexResetMonitor.isEnabled(),
                 force: Bool = false, now: Date = Date()) {
        guard enabled else {
            if requestTask != nil { lastAttempt = .distantPast }
            requestID = nil
            requestTask?.cancel()
            requestTask = nil
            isRefreshing = false
            return
        }
        // Manual retries are bounded too; regular collector ticks never cause extra traffic.
        let interval: TimeInterval = force ? 30 : (lastFetchFailed ? retryInterval : Self.refreshInterval)
        guard !isRefreshing, now.timeIntervalSince(lastAttempt) >= interval else { return }
        lastAttempt = now
        isRefreshing = true
        let id = UUID()
        requestID = id
        requestTask = CodexResetFeedRequest(configuration: sessionConfiguration) { [weak self] result in
            DispatchQueue.main.async {
                guard let self, self.requestID == id else { return }
                self.requestTask = nil
                self.requestID = nil
                self.isRefreshing = false
                switch result {
                case .success(let feed):
                    let snapshot = Snapshot(feed: feed, fetchedAt: Date())
                    self.snapshot = snapshot
                    self.lastFetchFailed = false
                    self.lastFailure = nil
                    self.failureCount = 0
                    if let cacheURL = self.cacheURL, let data = try? JSONEncoder().encode(snapshot) {
                        try? FileManager.default.createDirectory(at: cacheURL.deletingLastPathComponent(),
                                                               withIntermediateDirectories: true)
                        try? data.write(to: cacheURL, options: .atomic)
                    }
                case .failure(let error):
                    self.lastFetchFailed = true
                    self.lastFailure = error as? Failure ?? .invalidResponse
                    self.failureCount += 1
                }
            }
        }
        requestTask?.start()
    }

    deinit { requestTask?.cancel() }

    /// Deterministic offscreen previews; does not write cache or start networking.
    func usePreview(_ feed: CodexResetFeed, at now: Date = Date()) {
        snapshot = Snapshot(feed: feed, fetchedAt: now)
        lastFetchFailed = false
    }
}

/// One request, one serial delegate queue, and at most responseByteLimit retained bytes.
/// Invalidating the session also releases its strong reference to this delegate.
private final class CodexResetFeedRequest: NSObject, URLSessionDataDelegate {
    private let configuration: URLSessionConfiguration
    private let completion: (Result<CodexResetFeed, Error>) -> Void
    private lazy var session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
    private var body = Data()
    private var acceptedResponse = false
    private var finished = false
    private var redirectCount = 0

    init(configuration: URLSessionConfiguration,
         completion: @escaping (Result<CodexResetFeed, Error>) -> Void) {
        self.configuration = configuration
        self.completion = completion
    }

    func start() { session.dataTask(with: request(for: CodexResetMonitor.feedURL)).resume() }

    // URLSession cancellation is thread-safe. All response state stays on its serial delegate queue.
    func cancel() { session.invalidateAndCancel() }

    private func request(for url: URL) -> URLRequest {
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        // Keep redirects anonymous as well; do not reuse headers/body supplied by a redirect.
        request.setValue("Mozilla/5.0 (Macintosh) Tokei", forHTTPHeaderField: "User-Agent")
        return request
    }

    private func allowed(_ url: URL?) -> Bool {
        guard let url, url.scheme?.lowercased() == "https",
              url.host?.lowercased() == CodexResetMonitor.feedURL.host?.lowercased(),
              (url.port ?? 443) == (CodexResetMonitor.feedURL.port ?? 443),
              url.user == nil, url.password == nil else { return false }
        return true
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        guard !finished, allowed(newRequest.url), redirectCount < 5 else {
            completionHandler(nil)
            finish(.failure(CodexResetMonitor.Failure.invalidResponse))
            return
        }
        redirectCount += 1
        completionHandler(request(for: newRequest.url!))
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard !finished else { completionHandler(.cancel); return }
        let failure: CodexResetMonitor.Failure?
        if let http = response as? HTTPURLResponse {
            if http.statusCode != 200 { failure = .http(http.statusCode) }
            else if !allowed(http.url) || http.mimeType != "application/json" ||
                        http.expectedContentLength > Int64(CodexResetMonitor.responseByteLimit) {
                failure = .invalidResponse
            } else { failure = nil }
        } else { failure = .invalidResponse }
        if let failure {
            completionHandler(.cancel)
            finish(.failure(failure))
        } else {
            acceptedResponse = true
            completionHandler(.allow)
        }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard !finished else { return }
        // Check BEFORE append, including unknown/incorrect Content-Length and decoded body bytes.
        guard acceptedResponse, data.count <= CodexResetMonitor.responseByteLimit - body.count else {
            finish(.failure(CodexResetMonitor.Failure.invalidResponse))
            return
        }
        body.append(data)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard !finished else { return }
        if let error {
            finish(.failure(CodexResetMonitor.Failure.network((error as NSError).code)))
        } else if !acceptedResponse {
            finish(.failure(CodexResetMonitor.Failure.invalidResponse))
        } else {
            do { finish(.success(try CodexResetFeed.decode(body))) }
            catch { finish(.failure(CodexResetMonitor.Failure.invalidData)) }
        }
    }

    private func finish(_ result: Result<CodexResetFeed, Error>) {
        guard !finished else { return }
        finished = true
        body.removeAll(keepingCapacity: false)
        session.invalidateAndCancel()
        completion(result)
    }
}
