import { vi } from 'vitest'

// Polyfill ResizeObserver for jsdom (used by ChartRenderer)
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)
