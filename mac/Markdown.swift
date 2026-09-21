// Just enough Markdown.
//
// Models answer in Markdown whether or not you asked them to, so rendering it
// is not a nicety: unrendered, every answer arrives full of asterisks and
// fence backticks. This handles the four things that actually show up —
// headings, lists, inline emphasis and code — and deliberately stops there.
import AppKit
import SwiftUI

enum Block: Identifiable {
    case text(String)
    case code(String, language: String)
    case heading(String, level: Int)
    case bullet([String])

    var id: String {
        switch self {
        case .text(let s): return "t" + s
        case .code(let s, let l): return "c" + l + s
        case .heading(let s, let l): return "h\(l)" + s
        case .bullet(let items): return "b" + items.joined()
        }
    }
}

enum MarkdownParser {
    static func blocks(_ raw: String) -> [Block] {
        var out: [Block] = []
        var paragraph: [String] = []
        var bullets: [String] = []
        var code: [String] = []
        var language = ""
        var inCode = false

        func flushParagraph() {
            let joined = paragraph.joined(separator: "\n")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty { out.append(.text(joined)) }
            paragraph = []
        }
        func flushBullets() {
            if !bullets.isEmpty { out.append(.bullet(bullets)); bullets = [] }
        }

        for line in raw.components(separatedBy: .newlines) {
            if line.trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                if inCode {
                    out.append(.code(code.joined(separator: "\n"), language: language))
                    code = []; language = ""; inCode = false
                } else {
                    flushParagraph(); flushBullets()
                    language = String(line.trimmingCharacters(in: .whitespaces)
                        .dropFirst(3)).trimmingCharacters(in: .whitespaces)
                    inCode = true
                }
                continue
            }
            if inCode { code.append(line); continue }

            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty {
                flushParagraph(); flushBullets()
                continue
            }
            if trimmed.hasPrefix("#") {
                flushParagraph(); flushBullets()
                let hashes = trimmed.prefix { $0 == "#" }.count
                out.append(.heading(
                    String(trimmed.dropFirst(hashes)).trimmingCharacters(in: .whitespaces),
                    level: hashes))
                continue
            }
            if let item = bulletBody(trimmed) {
                flushParagraph()
                bullets.append(item)
                continue
            }
            flushBullets()
            paragraph.append(line)
        }
        if inCode && !code.isEmpty {
            // an answer that was cut off mid-fence still shows its code
            out.append(.code(code.joined(separator: "\n"), language: language))
        }
        flushParagraph(); flushBullets()
        return out
    }

    /// "- thing", "* thing", "1. thing" → "thing"
    private static func bulletBody(_ line: String) -> String? {
        for marker in ["- ", "* ", "• "] where line.hasPrefix(marker) {
            return String(line.dropFirst(marker.count))
        }
        let digits = line.prefix { $0.isNumber }
        if !digits.isEmpty {
            let rest = line.dropFirst(digits.count)
            if rest.hasPrefix(". ") || rest.hasPrefix(") ") {
                return "\(digits). " + rest.dropFirst(2)
            }
        }
        return nil
    }

    /// Splits "3. thing" into its own marker; anything else gets a bullet.
    static func marker(_ item: String) -> (marker: String, body: String) {
        let digits = item.prefix { $0.isNumber }
        if !digits.isEmpty, item.dropFirst(digits.count).hasPrefix(". ") {
            return ("\(digits).", String(item.dropFirst(digits.count + 2)))
        }
        return ("•", item)
    }

    /// Inline emphasis and `code`, with the raw text as the fallback.
    static func inline(_ text: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(
            allowsExtendedAttributes: true,
            interpretedSyntax: .inlineOnlyPreservingWhitespace)
        guard var attributed = try? AttributedString(markdown: text, options: options) else {
            return AttributedString(text)
        }
        // SwiftUI renders `code` in the body font unless told otherwise
        for run in attributed.runs where run.inlinePresentationIntent == .code {
            attributed[run.range].font = .hubMono
            attributed[run.range].foregroundColor = Palette.accent
        }
        return attributed
    }
}

struct MarkdownText: View {
    let content: String

    var body: some View {
        VStack(alignment: .leading, spacing: 11) {
            ForEach(MarkdownParser.blocks(content)) { block in
                switch block {
                case .text(let text):
                    Text(MarkdownParser.inline(text))
                        .font(.hubMessage)
                        .lineSpacing(4.5)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)

                case .heading(let text, let level):
                    Text(MarkdownParser.inline(text))
                        .font(.system(size: level <= 2 ? 16 : 14.5, weight: .semibold))
                        .padding(.top, 3)
                        .textSelection(.enabled)

                case .bullet(let items):
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                            // an ordered item carries its own number; giving it
                            // a bullet too reads as "• 4. Retry logic"
                            let split = MarkdownParser.marker(item)
                            HStack(alignment: .firstTextBaseline, spacing: 9) {
                                Text(split.marker)
                                    .font(.hubMessage)
                                    .foregroundStyle(Palette.inkFaint)
                                    .frame(minWidth: split.marker == "•" ? 0 : 17,
                                           alignment: .trailing)
                                Text(MarkdownParser.inline(split.body))
                                    .font(.hubMessage)
                                    .lineSpacing(4)
                                    .textSelection(.enabled)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }

                case .code(let code, let language):
                    CodeBlock(code: code, language: language)
                }
            }
        }
    }
}

struct CodeBlock: View {
    let code: String
    let language: String
    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(language.isEmpty ? "code" : language)
                    .font(.system(size: 10.5, weight: .medium))
                    .foregroundStyle(Palette.inkFaint)
                Spacer()
                Button(copied ? "copied" : "copy") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(code, forType: .string)
                    copied = true
                    Task {
                        try? await Task.sleep(for: .seconds(1.5))
                        copied = false
                    }
                }
                .buttonStyle(GhostButton())
            }
            .padding(.horizontal, 11)
            .padding(.vertical, 5)
            .background(Palette.fill.opacity(0.7))

            ScrollView(.horizontal, showsIndicators: false) {
                Text(code)
                    .font(.hubMono)
                    .textSelection(.enabled)
                    .padding(11)
            }
        }
        .background(Palette.fill.opacity(0.45))
        .clipShape(RoundedRectangle(cornerRadius: Metric.smallRadius))
        .overlay(RoundedRectangle(cornerRadius: Metric.smallRadius)
            .strokeBorder(Palette.hairline, lineWidth: 1))
    }
}
