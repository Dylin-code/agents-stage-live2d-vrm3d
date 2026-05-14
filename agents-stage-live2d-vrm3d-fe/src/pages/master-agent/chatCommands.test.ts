import { describe, expect, it } from 'vitest'
import { parseChatCommand } from './chatCommands'

describe('parseChatCommand', () => {
  it('recognizes bare #new as the new-conversation command', () => {
    const result = parseChatCommand('#new')
    expect(result.command).toBe('new')
    expect(result.remainder).toBe('')
    expect(result.permitFullAccess).toBe(false)
  })

  it('extracts remainder text after #new', () => {
    const result = parseChatCommand('#new 幫我看 codex 最新 session')
    expect(result.command).toBe('new')
    expect(result.remainder).toBe('幫我看 codex 最新 session')
  })

  it('is case-insensitive', () => {
    const result = parseChatCommand('#NEW hello')
    expect(result.command).toBe('new')
    expect(result.remainder).toBe('hello')
  })

  it('trims whitespace before matching', () => {
    const result = parseChatCommand('   #new   hi  ')
    expect(result.command).toBe('new')
    expect(result.remainder).toBe('hi')
  })

  it('returns null command for normal input', () => {
    const result = parseChatCommand('hello world')
    expect(result.command).toBeNull()
    expect(result.remainder).toBe('hello world')
  })

  it('does not match #new inside a longer word', () => {
    const result = parseChatCommand('#newcomer should not match')
    expect(result.command).toBeNull()
    expect(result.remainder).toBe('#newcomer should not match')
  })

  it('does not match #new in the middle of a sentence', () => {
    const result = parseChatCommand('please #new this')
    expect(result.command).toBeNull()
  })

  it('detects #full anywhere in the message and strips it', () => {
    const result = parseChatCommand('clean the build dir #full')
    expect(result.command).toBeNull()
    expect(result.permitFullAccess).toBe(true)
    expect(result.remainder).toBe('clean the build dir')
  })

  it('detects #full at the start', () => {
    const result = parseChatCommand('#full clean the build dir')
    expect(result.permitFullAccess).toBe(true)
    expect(result.remainder).toBe('clean the build dir')
  })

  it('composes #new and #full', () => {
    const result = parseChatCommand('#new #full clean repo')
    expect(result.command).toBe('new')
    expect(result.permitFullAccess).toBe(true)
    expect(result.remainder).toBe('clean repo')
  })

  it('does not match #full inside a longer word', () => {
    const result = parseChatCommand('please #fullscreen this')
    expect(result.permitFullAccess).toBe(false)
    expect(result.remainder).toBe('please #fullscreen this')
  })

  it('returns permitFullAccess=false for plain message', () => {
    const result = parseChatCommand('just a normal message')
    expect(result.permitFullAccess).toBe(false)
  })
})
