import { render, screen, waitFor } from '@testing-library/react'
import { describe, test, expect, beforeEach, afterEach, vi } from 'vitest'
import TimelineSection from '@/src/components/case/TimelineSection'

vi.mock('@/components/TimelineClient', () => ({
  default: ({ events }: { events: unknown[] }) => <div data-testid="timeline-client">{events.length} events</div>,
}))

const CASE_ID = '8f339cb1-de8b-4df4-9e88-9663f9cf0d11'

function mockFetch(status: number, body: unknown = {}) {
  const fn = vi.fn().mockResolvedValue({ ok: status >= 200 && status < 300, status, json: async () => body })
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('TimelineSection', () => {
  beforeEach(() => { vi.unstubAllGlobals() })
  afterEach(() => { vi.unstubAllGlobals() })

  test('sends the bearer token with the timeline request', async () => {
    const fetchMock = mockFetch(200, { events: [] })
    render(<TimelineSection caseId={CASE_ID} token="tok-123" />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain(`/case/${CASE_ID}/timeline`)
    expect(init.headers).toEqual({ Authorization: 'Bearer tok-123' })
  })

  test('renders events returned by the API', async () => {
    mockFetch(200, { events: [{ date: '2026-08-10' }, { date: '2026-09-15' }] })
    render(<TimelineSection caseId={CASE_ID} token="tok-123" />)
    expect(await screen.findByTestId('timeline-client')).toHaveTextContent('2 events')
  })

  test('shows a load error, not "no events", when the API rejects auth', async () => {
    mockFetch(401)
    render(<TimelineSection caseId={CASE_ID} token="expired" />)
    expect(await screen.findByText('Could not load timeline.')).toBeInTheDocument()
    expect(screen.queryByText(/No timeline events found/)).not.toBeInTheDocument()
  })

  test('shows "no events" when the case has none', async () => {
    mockFetch(404)
    render(<TimelineSection caseId={CASE_ID} token="tok-123" />)
    expect(await screen.findByText('No timeline events found for this case.')).toBeInTheDocument()
  })

  test('does not call the API without a token', () => {
    const fetchMock = mockFetch(200, { events: [] })
    render(<TimelineSection caseId={CASE_ID} token={null} />)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
