/**
 * Terminal theme catalog — wraps xterm-theme package.
 *
 * Each theme is a plain object whose keys match xterm.js ITheme
 * (foreground, background, cursor, black … brightWhite).
 *
 * NOTE: the localStorage key is intentionally still ``web-terminal-theme``
 * to preserve user-selected themes carried over from the old WebTerminal
 * component this module used to live next to.
 */

// @ts-expect-error — untyped JS package
import * as allThemes from '../../../xterm-theme/src/index.js'
import type { ITheme } from '@xterm/xterm'

const STORAGE_KEY = 'web-terminal-theme'

/** Catppuccin Mocha — built-in default when no theme is selected */
export const DEFAULT_THEME: ITheme = {
  background: '#1e1e2e',
  foreground: '#cdd6f4',
  cursor: '#f5e0dc',
  selectionBackground: '#585b7066',
  black: '#45475a',
  red: '#f38ba8',
  green: '#a6e3a1',
  yellow: '#f9e2af',
  blue: '#89b4fa',
  magenta: '#f5c2e7',
  cyan: '#94e2d5',
  white: '#bac2de',
  brightBlack: '#585b70',
  brightRed: '#f38ba8',
  brightGreen: '#a6e3a1',
  brightYellow: '#f9e2af',
  brightBlue: '#89b4fa',
  brightMagenta: '#f5c2e7',
  brightCyan: '#94e2d5',
  brightWhite: '#a6adc8',
}

const DEFAULT_NAME = 'Catppuccin Mocha'

/** Sorted theme name list (default entry first). */
export const themeNames: string[] = (() => {
  const names = Object.keys(allThemes).sort((a, b) => a.localeCompare(b))
  return [DEFAULT_NAME, ...names]
})()

/** Resolve a theme name to an ITheme object. */
export function getTheme(name: string): ITheme {
  if (name === DEFAULT_NAME) return DEFAULT_THEME
  const raw = (allThemes as Record<string, Record<string, string>>)[name]
  return raw ? (raw as unknown as ITheme) : DEFAULT_THEME
}

/** Load persisted theme name from localStorage. */
export function loadThemeName(): string {
  return localStorage.getItem(STORAGE_KEY) || DEFAULT_NAME
}

/** Persist theme name to localStorage. */
export function saveThemeName(name: string): void {
  localStorage.setItem(STORAGE_KEY, name)
}
