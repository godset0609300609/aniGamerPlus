/**
 * Splits an RSS entry title into bracket tokens ("[...]" / "【...】" contents)
 * and free-text tokens (whatever's left, split on whitespace/_/-/+// /|).
 *
 * Bracket groups containing "/" or "|" carry multiple alternative titles
 * (common in BT torrent names, e.g. multi-language aliases) and are further
 * split on those two characters only, so compound tokens like
 * "WebRip 1080p HEVC-10bit AAC" stay intact.
 *
 * Used by the BT filter "從標題匯入" wizard to turn a picked feed entry's
 * title into a starting set of filter keywords.
 */

export interface TokenizedTitle {
  bracket: string[]
  freeText: string[]
}

const BRACKET_PATTERN = /\[([^[\]]*)\]|【([^【】]*)】/g
const BRACKET_INNER_SPLIT_PATTERN = /[/|]/
const FREE_TEXT_SPLIT_PATTERN = /[\s_\-+/|]+/

function isKeepable(token: string): boolean {
  return token.trim().length > 1
}

export function tokenizeTitle(title: string): TokenizedTitle {
  const bracket: string[] = []

  const remainder = title.replace(BRACKET_PATTERN, (_match, square, cjk) => {
    const content = ((square ?? cjk ?? '') as string).trim()
    if (isKeepable(content)) {
      content
        .split(BRACKET_INNER_SPLIT_PATTERN)
        .map((token) => token.trim())
        .filter(isKeepable)
        .forEach((token) => bracket.push(token))
    }
    return ' '
  })

  const freeText = remainder
    .split(FREE_TEXT_SPLIT_PATTERN)
    .map((token) => token.trim())
    .filter(isKeepable)

  return { bracket, freeText }
}
