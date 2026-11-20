import { createContext, useContext, useState, type ReactNode } from 'react'

export interface TicketRef { tenant: string; ticket: string }

const Ctx = createContext<{
  detail: TicketRef | null
  open: (t: TicketRef | null) => void
}>({ detail: null, open: () => {} })

export function TicketProvider({ children }: { children: ReactNode }) {
  const [detail, setDetail] = useState<TicketRef | null>(null)
  return <Ctx.Provider value={{ detail, open: setDetail }}>{children}</Ctx.Provider>
}

export const useTicketDetail = () => useContext(Ctx)
