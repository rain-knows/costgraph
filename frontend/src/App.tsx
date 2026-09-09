import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'

const DashboardPage = lazy(() => import('./pages/DashboardPage').then((module) => ({ default: module.DashboardPage })))
const CostDataPage = lazy(() => import('./pages/CostDataPage').then((module) => ({ default: module.CostDataPage })))
const RecordDetailPage = lazy(() => import('./pages/RecordDetailPage').then((module) => ({ default: module.RecordDetailPage })))
const AiWorkspacePage = lazy(() => import('./pages/AiWorkspacePage').then((module) => ({ default: module.AiWorkspacePage })))
const ReportsPage = lazy(() => import('./pages/ReportsPage').then((module) => ({ default: module.ReportsPage })))

function PageLoading() {
  return <div className="grid min-h-[calc(100vh-48px)] place-items-center" role="status"><span className="label">正在加载工作区</span></div>
}

export default function App() {
  return <Suspense fallback={<PageLoading />}><Routes>
    <Route element={<AppShell />}>
      <Route path="/" element={<DashboardPage />} />
      <Route path="/cost-data" element={<CostDataPage />} />
      <Route path="/cost-data/:finishedBatchId" element={<RecordDetailPage />} />
      <Route path="/ai" element={<AiWorkspacePage />} />
      <Route path="/ai/:conversationId" element={<AiWorkspacePage />} />
      <Route path="/reports" element={<ReportsPage />} />
      <Route path="/reports/:artifactId" element={<ReportsPage />} />
    </Route>
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes></Suspense>
}
