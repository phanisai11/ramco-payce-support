import { useCallback, useEffect, useState } from 'react'
import { Sidebar, type TabId } from './components/Sidebar'
import { Header } from './components/Header'
import { TicketDetail } from './components/TicketDetail'
import { TicketProvider, useTicketDetail } from './ticket'
import { api } from './api'
import { Home } from './pages/Home'
import { Ops } from './pages/Ops'
import { Intake } from './pages/Intake'
import { Tickets } from './pages/Tickets'
import { Channels } from './pages/Channels'
import { Worklist } from './pages/Worklist'
import { Sla } from './pages/Sla'
import { Rules } from './pages/Rules'
import { SlaRules } from './pages/SlaRules'
import { Kb } from './pages/Kb'
import { Csat } from './pages/Csat'

function Shell() {
  const [tab, setTab] = useState<TabId>('home')
  const [tenants, setTenants] = useState<string[]>([])
  const [names, setNames] = useState<Record<string, string>>({})
  const [refreshKey, setRefreshKey] = useState(0)
  const [ticketPreset, setTicketPreset] = useState<{ tenant: string; channel: string } | null>(null)
  const { detail } = useTicketDetail()

  useEffect(() => {
    api.policy().then((p) => {
      const ctx = p.tenant_contexts ?? {}
      setTenants(Object.keys(ctx))
      setNames(Object.fromEntries(Object.entries(ctx).map(([k, v]) => [k, v.display_name ?? k])))
    }).catch(() => {})
  }, [])

  const bump = useCallback(() => setRefreshKey((k) => k + 1), [])

  return (
    <div className="flex h-screen overflow-hidden bg-ink-50">
      <Sidebar tab={tab} onTab={setTab} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header />
        <main className="flex-1 overflow-y-auto px-6 py-5">
          {tab === 'home' && <Home tenants={tenants} names={names} />}
          {tab === 'ops' && <Ops tenants={tenants} names={names} />}
          {tab === 'intake' && <Intake tenants={tenants} names={names} onCreated={bump} />}
          {tab === 'tickets' && (
            <Tickets tenants={tenants} names={names} refreshKey={refreshKey}
              defaultTenant={ticketPreset?.tenant ?? ''}
              presetChannel={ticketPreset?.channel ?? ''}
              onPresetConsumed={() => setTicketPreset(null)} />
          )}
          {tab === 'channels' && (
            <Channels tenants={tenants} names={names}
              onFilter={(tenant, channel) => { setTicketPreset({ tenant, channel }); setTab('tickets') }} />
          )}
          {tab === 'worklist' && <Worklist tenants={tenants} names={names} refreshKey={refreshKey} />}
          {tab === 'sla' && <Sla tenants={tenants} names={names} />}
          {tab === 'csat' && <Csat tenants={tenants} names={names} />}
          {tab === 'rules' && <Rules tenants={tenants} names={names} />}
          {tab === 'sla-rules' && <SlaRules tenants={tenants} names={names} />}
          {tab === 'kb' && <Kb />}
          {detail && (
            <div className="fixed inset-0 z-40 overflow-y-auto bg-ink-50/95 p-6 backdrop-blur-sm">
              <div className="mx-auto max-w-[1400px]">
                <TicketDetail tenant={detail.tenant} ticketId={detail.ticket} names={names} onChanged={bump} />
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <TicketProvider>
      <Shell />
    </TicketProvider>
  )
}
