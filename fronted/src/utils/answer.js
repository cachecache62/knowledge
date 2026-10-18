function imageUrl(raw) {
  try {
    const url = new URL(String(raw || '').trim().replace(/\s/g, '%20'))
    if (!['http:', 'https:'].includes(url.protocol)) return null
    return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(url.pathname) ? url.href : null
  } catch {
    return null
  }
}

function urlsFromText(text) {
  return (text.match(/https?:\/\/[^\s]+/g) || []).map((url) => url.replace(/[)\]}'">，。,;；】）＞]+$/g, ''))
}

// 支持原页面的【图片】区块、Markdown 图片以及后端 image_urls。
export function parseAnswer(answer, candidates = []) {
  const raw = String(answer || '')
  const markers = [...raw.matchAll(/【\s*图片\s*】|\[\s*图片\s*\]/g)]
  const marker = markers.at(-1)
  const block = marker ? raw.slice(marker.index + marker[0].length) : ''
  const markdownUrls = [...raw.matchAll(/!\[[^\]]*\]\(([^\n]+?)\)/g)].map((match) => match[1])
  const blockUrls = block.split(/\r?\n/).map((line) => line.trim()).filter((line) => /^https?:\/\//.test(line))
  const images = [...new Set([
    ...blockUrls,
    ...(Array.isArray(candidates) ? candidates : []),
    ...markdownUrls,
    ...urlsFromText(raw),
  ].map(imageUrl).filter(Boolean))]
  return {
    text: marker && images.length ? raw.slice(0, marker.index).trimEnd() : raw,
    images,
  }
}
