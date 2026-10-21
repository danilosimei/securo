import { describe, expect, it } from 'vitest'
import { AxiosError, AxiosHeaders } from 'axios'

import {
  ASK_MAX_PERSON_MESSAGES,
  askBlocked,
  askErrorKey,
  personCount,
  toWire,
  type ChatTurn,
} from '@/lib/categorization-ask'
import type { AskResponse, CategorizationSummary } from '@/types'

function summary(overrides: Partial<CategorizationSummary> = {}): CategorizationSummary {
  return {
    pending_groups: 1,
    pending_transactions: 3,
    applied_last_7d: 0,
    last_run: null,
    ai_available: true,
    ai_enabled: true,
    month_cost_usd: '0.10',
    monthly_cap_usd: '2.00',
    ...overrides,
  }
}

function apiError(status: number, detail: unknown) {
  const headers = new AxiosHeaders()
  return new AxiosError('failed', String(status), undefined, undefined, {
    status,
    statusText: '',
    headers,
    config: { headers },
    data: { detail },
  })
}

const answer: AskResponse = {
  reply: 'Jardinagem, então.',
  suggestion: null,
  note: null,
  assistant_turn: '{"reply":"Jardinagem, então."}',
  person_messages_left: 29,
}

describe('askBlocked', () => {
  it('libera com tudo certo', () => {
    expect(askBlocked(summary(), true)).toBeNull()
  })

  it('sem summary ainda não bloqueia quem escreve', () => {
    expect(askBlocked(undefined, true)).toBeNull()
  })

  it('quem só lê vem primeiro', () => {
    expect(askBlocked(summary({ ai_enabled: false, ai_available: false }), false)).toBe('readOnly')
    expect(askBlocked(undefined, false)).toBe('readOnly')
  })

  it('IA desligada', () => {
    expect(askBlocked(summary({ ai_enabled: false, ai_available: false }), true)).toBe('disabled')
  })

  it('sem conexão', () => {
    expect(askBlocked(summary({ ai_available: false }), true)).toBe('noConnection')
  })

  it('teto do mês atingido', () => {
    expect(askBlocked(summary({ month_cost_usd: '2.00', monthly_cap_usd: '2' }), true)).toBe('capped')
    expect(askBlocked(summary({ month_cost_usd: 1.99, monthly_cap_usd: 2 }), true)).toBeNull()
  })
})

describe('toWire / personCount', () => {
  const turns: ChatTurn[] = [
    { role: 'user', text: 'é o jardineiro', wire: 'é o jardineiro' },
    { role: 'assistant', text: answer.reply, wire: answer.assistant_turn, answer },
    { role: 'user', text: 'toda semana', wire: 'toda semana' },
  ]

  it('o turno do assistente vai como assistant_turn', () => {
    expect(toWire(turns)).toEqual([
      { role: 'user', content: 'é o jardineiro' },
      { role: 'assistant', content: answer.assistant_turn },
      { role: 'user', content: 'toda semana' },
    ])
  })

  it('conta só as mensagens da pessoa', () => {
    expect(personCount(turns)).toBe(2)
    expect(ASK_MAX_PERSON_MESSAGES).toBe(30)
  })
})

describe('askErrorKey', () => {
  it('409 conversation_limit', () => {
    expect(askErrorKey(apiError(409, { code: 'conversation_limit', message: 'x' }))).toBe(
      'categorization.ask.errors.conversation_limit',
    )
  })

  it.each(['capped', 'disabled', 'no_connection', 'timeout', 'model_error'])('código %s', (code) => {
    expect(askErrorKey(apiError(503, { code, message: 'x' }))).toBe(`categorization.ask.errors.${code}`)
  })

  it('429 é rateLimited', () => {
    expect(askErrorKey(apiError(429, { code: 'rate_limited', message: 'x' }))).toBe(
      'categorization.ask.errors.rateLimited',
    )
    expect(askErrorKey(apiError(429, 'Too many requests'))).toBe('categorization.ask.errors.rateLimited')
  })

  it('504 sem código (proxy) ainda é timeout', () => {
    expect(askErrorKey(apiError(504, 'Gateway Timeout'))).toBe('categorization.ask.errors.timeout')
  })

  it('o resto é genérico', () => {
    expect(askErrorKey(apiError(404, { code: 'not_found', message: 'x' }))).toBe(
      'categorization.ask.errors.generic',
    )
    expect(askErrorKey(new Error('network'))).toBe('categorization.ask.errors.generic')
    expect(askErrorKey(undefined)).toBe('categorization.ask.errors.generic')
  })
})
