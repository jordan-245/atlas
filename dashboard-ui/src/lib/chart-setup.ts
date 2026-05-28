/**
 * chart-setup.ts -- One-time Chart.js component registration.
 *
 * Chart.js requires manual registration of the controllers / elements /
 * scales / plugins you actually use (this is what gives it good
 * tree-shaking).  Import this module exactly once -- it's a side-effect
 * import -- from the shared Chart wrapper.
 *
 * Adding a new chart type?  Register its controller + any new element
 * here, not in the call site.  Keeps Chart.js bundle predictable.
 */

import {
  Chart as ChartJS,
  // Controllers
  LineController,
  BarController,
  DoughnutController,
  // Elements
  LineElement,
  PointElement,
  BarElement,
  ArcElement,
  // Scales
  CategoryScale,
  LinearScale,
  TimeScale,
  // Plugins
  Tooltip,
  Legend,
  Filler,
  Title,
  SubTitle,
} from 'chart.js'

let _registered = false

export function ensureChartRegistered(): void {
  if (_registered) return
  ChartJS.register(
    LineController,
    BarController,
    DoughnutController,
    LineElement,
    PointElement,
    BarElement,
    ArcElement,
    CategoryScale,
    LinearScale,
    TimeScale,
    Tooltip,
    Legend,
    Filler,
    Title,
    SubTitle,
  )
  _registered = true
}
