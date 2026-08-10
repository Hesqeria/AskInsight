import { ref, watch } from 'vue'

export const lightTheme = {
  color: ['#409eff', '#67c23a', '#e6a23c', '#f56c6c', '#9254de', '#22c3e6', '#f89898', '#79bbff'],
  backgroundColor: 'transparent',
  textStyle: { color: '#333', fontFamily: 'system-ui, sans-serif' },
  title: { textStyle: { color: '#333' }, subtextStyle: { color: '#999' } },
  legend: { textStyle: { color: '#666' }, pageTextStyle: { color: '#666' } },
  tooltip: { backgroundColor: 'rgba(255,255,255,0.95)', borderColor: '#e5e7eb', textStyle: { color: '#333' } },
  axisLine: { lineStyle: { color: '#e5e7eb' } },
  axisTick: { lineStyle: { color: '#e5e7eb' } },
  axisLabel: { color: '#999' },
  splitLine: { lineStyle: { color: '#f0f0f0' } },
  splitArea: { areaStyle: { color: ['rgba(250,250,250,0.3)', 'rgba(245,245,245,0.3)'] } },
}

export const darkTheme = {
  color: ['#66b1ff', '#85ce61', '#ebb563', '#f78989', '#b37feb', '#48d1cc', '#fab6b6', '#a6d2ff'],
  backgroundColor: 'transparent',
  textStyle: { color: '#e0e0e0', fontFamily: 'system-ui, sans-serif' },
  title: { textStyle: { color: '#e0e0e0' }, subtextStyle: { color: '#a0a0b0' } },
  legend: { textStyle: { color: '#c0c0d0' }, pageTextStyle: { color: '#c0c0d0' } },
  tooltip: { backgroundColor: 'rgba(26,26,46,0.95)', borderColor: '#2a2a4a', textStyle: { color: '#e0e0e0' } },
  axisLine: { lineStyle: { color: '#2a2a4a' } },
  axisTick: { lineStyle: { color: '#2a2a4a' } },
  axisLabel: { color: '#a0a0b0' },
  splitLine: { lineStyle: { color: '#16213e' } },
  splitArea: { areaStyle: { color: ['rgba(22,33,62,0.3)', 'rgba(15,52,96,0.3)'] } },
}

const isDark = ref(false)

export function useEchartsTheme() {
  function sync() {
    isDark.value = document.documentElement.getAttribute('data-theme') === 'dark'
  }

  function watchTheme() {
    const observer = new MutationObserver(sync)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    sync()
    return () => observer.disconnect()
  }

  function getTheme() {
    return isDark.value ? darkTheme : lightTheme
  }

  return { isDark, getTheme, watchTheme, sync }
}
