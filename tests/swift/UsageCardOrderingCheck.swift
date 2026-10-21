import Foundation

private struct TokenCase: Decodable {
    let id: String
    let range: String
    let tokens: Int
}

private struct OrderCase: Decodable {
    let range: String
    let ids: [String]
    let expected: [String]
}

private struct Fixture: Decodable {
    let usage: Usage
    let cases: [TokenCase]
    let orders: [OrderCase]
}

private enum CheckFailure: Error {
    case mismatch(String)
}

@main
struct UsageCardOrderingCheck {
    static func main() throws {
        let fixture = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
        for test in fixture.cases {
            let actual = UsageCardOrdering.tokens(for: test.id, usage: fixture.usage, range: RangeKey(rawValue: test.range)!)
            guard actual == test.tokens else {
                throw CheckFailure.mismatch("\(test.id)/\(test.range): \(actual), expected \(test.tokens)")
            }
        }
        for test in fixture.orders {
            let range = RangeKey(rawValue: test.range)!
            let actual = UsageCardOrdering.sorted(test.ids) { UsageCardOrdering.tokens(for: $0, usage: fixture.usage, range: range) }
            guard actual == test.expected else {
                throw CheckFailure.mismatch("\(test.range): \(actual), expected \(test.expected)")
            }
        }
        print("usage card ordering checks passed (\(fixture.cases.count) totals, \(fixture.orders.count) orders)")
    }
}
