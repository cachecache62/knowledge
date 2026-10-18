// 返回结构化数据，由 Vue 文本插值渲染；不将模型返回的 HTML 注入页面。
export function inlineTokens(text) {
  const source = String(text || '')
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g
  const tokens = []
  let cursor = 0
  for (const match of source.matchAll(pattern)) {
    if (match.index > cursor) tokens.push({ type: 'text', text: source.slice(cursor, match.index) })
    const value = match[0]
    if (value.startsWith('`')) tokens.push({ type: 'code', text: value.slice(1, -1) })
    else if (value.startsWith('**')) tokens.push({ type: 'strong', text: value.slice(2, -2) })
    else {
      const link = value.match(/^\[([^\]]+)\]\((.+)\)$/)
      try {
        const url = new URL(link[2])
        tokens.push({ type: 'link', text: link[1], href: url.href })
      } catch { tokens.push({ type: 'text', text: value }) }
    }
    cursor = match.index + value.length
  }
  if (cursor < source.length) tokens.push({ type: 'text', text: source.slice(cursor) })
  return tokens
}

export function markdownBlocks(text) {
  const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n')
  const blocks = []
  let index = 0
  const startsBlock = line => /^\s*(`{3,}|~{3,})|^#{1,6}\s|^\s*([-*+]\s+|\d+[.)]\s+)|^>\s?|^\s*(---+|\*\*\*+)\s*$/.test(line)
  while (index < lines.length) {
    const line = lines[index]
    if (!line.trim()) { index++; continue }
    const fence = line.match(/^\s*(`{3,}|~{3,})(.*)$/)
    if (fence) {
      const code = []
      const closing = new RegExp(`^\\s*${fence[1][0]}{${fence[1].length},}\\s*$`)
      index++
      while (index < lines.length && !closing.test(lines[index])) code.push(lines[index++])
      if (index < lines.length) index++
      blocks.push({ type: 'code', language: fence[2].trim() || 'text', text: code.join('\n') })
      continue
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/)
    if (heading) { blocks.push({ type: 'heading', level: heading[1].length, text: heading[2] }); index++; continue }
    if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) { blocks.push({ type: 'rule' }); index++; continue }
    const list = line.match(/^\s*([-*+]|\d+[.)])\s+(.+)$/)
    if (list) {
      const ordered = /^\d/.test(list[1])
      const items = []
      const start = ordered ? parseInt(list[1], 10) : undefined
      while (index < lines.length) {
        const item = lines[index].match(/^\s*([-*+]|\d+[.)])\s+(.+)$/)
        if (!item || /^\d/.test(item[1]) !== ordered) break
        items.push(item[2]); index++
      }
      blocks.push({ type: 'list', ordered, start, items }); continue
    }
    if (/^>\s?/.test(line)) {
      const quote = []
      while (index < lines.length && /^>\s?/.test(lines[index])) quote.push(lines[index++].replace(/^>\s?/, ''))
      blocks.push({ type: 'quote', text: quote.join('\n') }); continue
    }
    const paragraph = [line]
    index++
    while (index < lines.length && lines[index].trim() && !startsBlock(lines[index])) paragraph.push(lines[index++])
    blocks.push({ type: 'paragraph', text: paragraph.join('\n') })
  }
  return blocks
}
