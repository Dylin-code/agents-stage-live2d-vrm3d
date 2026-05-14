import { describe, expect, it } from 'vitest'
import { consumeMasterEventStream } from './masterAgent'

function makeSseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk))
      }
      controller.close()
    },
  })
  return new Response(stream, { status: 200 })
}

describe('consumeMasterEventStream', () => {
  it('parses well-formed SSE chunks into MasterEvent objects', async () => {
    const response = makeSseResponse([
      'data: {"type":"master_thinking_delta","content":{"text":"hi","hop":0}}\n\n',
      'data: {"type":"tool_call_begin","content":{"id":"t1","name":"report_to_user","arguments":{"text":"done"}}}\n\n',
      'data: {"type":"final_text","content":{"text":"done"}}\n\n',
    ])
    const events: unknown[] = []
    for await (const event of consumeMasterEventStream(response)) {
      events.push(event)
    }
    expect(events).toHaveLength(3)
    expect((events[0] as { type: string }).type).toBe('master_thinking_delta')
    expect((events[2] as { type: string }).type).toBe('final_text')
  })

  it('handles SSE chunks split across reader callbacks', async () => {
    const response = makeSseResponse([
      'data: {"type":"master_thinki',
      'ng_delta","content":{"text":"hello"}}',
      '\n\ndata: {"type":"final_text","content":{"text":"bye"}}\n\n',
    ])
    const events: unknown[] = []
    for await (const event of consumeMasterEventStream(response)) {
      events.push(event)
    }
    expect(events).toHaveLength(2)
    expect((events[0] as { type: string }).type).toBe('master_thinking_delta')
    expect((events[1] as { type: string }).type).toBe('final_text')
  })

  it('drops malformed JSON chunks instead of throwing', async () => {
    const response = makeSseResponse([
      'data: not json\n\n',
      'data: {"type":"final_text","content":{"text":"ok"}}\n\n',
    ])
    const events: unknown[] = []
    for await (const event of consumeMasterEventStream(response)) {
      events.push(event)
    }
    expect(events).toHaveLength(1)
    expect((events[0] as { type: string }).type).toBe('final_text')
  })
})
