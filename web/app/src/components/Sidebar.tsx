import {
  LayoutDashboard, Inbox, Ticket as TicketIcon, Share2, Users,
  Timer, Route, FileText, BookOpen, MonitorPlay, Star, type LucideIcon,
} from 'lucide-react'

export type TabId =
  | 'home' | 'ops' | 'intake' | 'tickets' | 'channels' | 'worklist'
  | 'sla' | 'rules' | 'sla-rules' | 'kb' | 'csat'

interface NavItem { id: TabId; label: string; icon: LucideIcon }
interface NavGroup { label: string; items: NavItem[] }

const GROUPS: NavGroup[] = [
  {
    label: 'Overview',
    items: [
      { id: 'home', label: 'Home', icon: LayoutDashboard },
      { id: 'ops', label: 'Command center', icon: MonitorPlay },
      { id: 'intake', label: 'Intake', icon: Inbox },
      { id: 'tickets', label: 'Tickets', icon: TicketIcon },
      { id: 'channels', label: 'Channels', icon: Share2 },
      { id: 'worklist', label: 'Worklist', icon: Users },
    ],
  },
  {
    label: 'Monitoring',
    items: [
      { id: 'sla', label: 'SLA monitoring', icon: Timer },
      { id: 'csat', label: 'CSAT', icon: Star },
    ],
  },
  {
    label: 'Configuration',
    items: [
      { id: 'rules', label: 'Rules & context', icon: Route },
      { id: 'sla-rules', label: 'SLA rules', icon: FileText },
      { id: 'kb', label: 'Knowledge base', icon: BookOpen },
    ],
  },
]

export function Sidebar({ tab, onTab }: { tab: TabId; onTab: (t: TabId) => void }) {
  return (
    <aside className="flex h-full w-60 shrink-0 flex-col border-r border-ink-200 bg-white">
      <div className="flex items-center gap-2.5 border-b border-ink-100 px-5 py-4">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-violet-500 text-sm font-extrabold text-white">P</span>
        <div className="leading-tight">
          <div className="text-[13px] font-bold text-ink-900">Payce Support</div>
          <div className="text-[10px] font-medium text-ink-400">Ops console</div>
        </div>
      </div>
      <nav className="flex-1 overflow-y-auto px-3 py-3">
        {GROUPS.map((g) => (
          <div key={g.label} className="mb-1">
            <div className="px-2 pb-1.5 pt-3 text-[10px] font-bold uppercase tracking-widest text-ink-400">
              {g.label}
            </div>
            {g.items.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => onTab(id)}
                className={`mb-0.5 flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] font-medium transition-colors ${
                  tab === id
                    ? 'bg-brand-50 font-semibold text-brand-500'
                    : 'text-ink-600 hover:bg-ink-100 hover:text-ink-900'
                }`}
              >
                <Icon size={16} strokeWidth={1.8} className={tab === id ? 'text-brand-500' : 'text-ink-400'} />
                {label}
              </button>
            ))}
          </div>
        ))}
      </nav>
      <div className="border-t border-ink-100 px-5 py-3 text-[10px] font-medium text-ink-400">
        v1.0 · local demo
      </div>
    </aside>
  )
}
