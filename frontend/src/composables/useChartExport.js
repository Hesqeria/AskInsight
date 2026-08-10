import { ref } from 'vue'

export function useChartExport() {
  const exporting = ref(false)

  function downloadPNG(chartInstance, filename = 'chart.png') {
    if (!chartInstance) return
    const url = chartInstance.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' })
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
  }

  function csvEscape(value) {
    const str = String(value ?? '')
    const newline = String.fromCharCode(10)
    const carriage = String.fromCharCode(13)
    if (str.includes(',') || str.includes('"') || str.includes(newline) || str.includes(carriage)) {
      return '"' + str.replace(/"/g, '""') + '"'
    }
    return str
  }

  function downloadCSV(rows, columns, filename = 'data.csv') {
    if (!rows?.length) return
    const cols = columns || Object.keys(rows[0])
    const header = cols.join(',')
    const lines = rows.map(r => cols.map(c => csvEscape(r[c])).join(','))
    const newline = String.fromCharCode(10)
    const bom = String.fromCharCode(0xFEFF)
    const blob = new Blob([bom + header + newline + lines.join(newline)], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }

  async function downloadDashboardPDF(elements, filename = 'dashboard.pdf') {
    exporting.value = true
    try {
      const html2canvas = (await import('html2canvas')).default
      const { jsPDF } = await import('jspdf')
      const pdf = new jsPDF('l', 'mm', 'a4')
      for (let i = 0; i < elements.length; i++) {
        const el = elements[i]
        if (!el) continue
        const canvas = await html2canvas(el, { scale: 2, backgroundColor: '#ffffff' })
        const img = canvas.toDataURL('image/png')
        const width = pdf.internal.pageSize.getWidth()
        const height = (canvas.height * width) / canvas.width
        if (i > 0) pdf.addPage()
        pdf.addImage(img, 'PNG', 0, 0, width, height)
      }
      pdf.save(filename)
    } finally {
      exporting.value = false
    }
  }

  async function copySQL(sql) {
    if (!sql) return
    try {
      await navigator.clipboard.writeText(sql)
    } catch {
      const ta = document.createElement('textarea')
      ta.value = sql
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
  }

  return { exporting, downloadPNG, downloadCSV, downloadDashboardPDF, copySQL }
}
