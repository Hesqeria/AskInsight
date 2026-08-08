export function useExport() {
  function exportCSV(columns, rows, filename) {
    filename = filename || 'result.csv'
    const lines = [columns.join(',')]
    rows.forEach(r => {
      lines.push(columns.map(c => JSON.stringify(r[c] != null ? r[c] : '')).join(','))
    })
    const blob = new Blob([String.fromCharCode(0xFEFF) + lines.join('\\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }

  function copyToClipboard(text) {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text)
    } else {
      const el = document.createElement('textarea')
      el.value = text
      document.body.appendChild(el)
      el.select()
      document.execCommand('copy')
      document.body.removeChild(el)
    }
  }

  return { exportCSV, copyToClipboard }
}
