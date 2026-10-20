import { render, screen, fireEvent } from '@testing-library/react'
import { describe, test, expect } from 'vitest'
import TimelineClient from '@/components/TimelineClient'
import type { CaseEvent } from '@/types/case'

const EVENT: CaseEvent = {
  id: 'e1',
  date: '2026-08-10',
  category: 'Court',
  event_type: 'hearing',
  subject: 'First directions hearing',
  summary: 'Directions hearing at NCAT.',
  content: 'Tribunal ordered exchange of evidence by 15 Sep.',
  attachments: [],
}

function openModal() {
  render(<TimelineClient events={[EVENT]} />)
  fireEvent.click(screen.getByTestId('timeline-row'))
}

describe('TimelineClient event modal', () => {
  test('no dialog until a row is clicked', () => {
    render(<TimelineClient events={[EVENT]} />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  test('opens as a modal dialog named by the event subject', () => {
    openModal()
    const dialog = screen.getByRole('dialog', { name: 'First directions hearing' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveTextContent('Tribunal ordered exchange of evidence')
  })

  test('close button has an accessible name and closes the dialog', () => {
    openModal()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  test('Escape closes the dialog', () => {
    openModal()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
