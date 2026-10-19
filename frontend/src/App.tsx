import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AuthCallbackPage } from './auth/AuthCallbackPage'
import { RequireSignIn } from './auth/RequireSignIn'
import { AppShell } from './components/AppShell'

const DashboardPage = lazy(() => import('./pages/DashboardPage').then((module) => ({ default: module.DashboardPage })))
const CostDataPage = lazy(() => import('./pages/CostDataPage').then((module) => ({ default: module.CostDataPage })))
const RecordDetailPage = lazy(() => import('./pages/RecordDetailPage').then((module) => ({ default: module.RecordDetailPage })))
const AiWorkspacePage = lazy(() => import('./pages/AiWorkspacePage').then((module) => ({ default: module.AiWorkspacePage })))
const ReportsPage = lazy(() => import('./pages/ReportsPage').then((module) => ({ default: module.ReportsPage })))
const DataAdminPage = lazy(() => import('./pages/DataAdminPage').then((module) => ({ default: module.DataAdminPage })))
const ImportJobsPage = lazy(() => import('./pages/ImportJobsPage').then((module) => ({ default: module.ImportJobsPage })))
const GovernanceSubmitPage = lazy(() => import('./pages/GovernanceSubmitPage').then((module) => ({ default: module.GovernanceSubmitPage })))
const ImportJobDetailPage = lazy(() => import('./pages/ImportJobDetailPage').then((module) => ({ default: module.ImportJobDetailPage })))
const IamPage = lazy(() => import('./pages/IamPage').then((module) => ({ default: module.IamPage })))
const AuditPage = lazy(() => import('./pages/AuditPage').then((module) => ({ default: module.AuditPage })))

function PageLoading() {
  return <div className="grid min-h-[calc(100vh-48px)] place-items-center" role="status"><span className="label">正在加载工作区</span></div>
}

export default function App() {
  return <Suspense fallback={<PageLoading />}><Routes>
    <Route path="/auth/callback" element={<AuthCallbackPage />} />
    <Route element={<RequireSignIn><AppShell /></RequireSignIn>}>
      <Route path="/" element={<DashboardPage />} />
      <Route path="/cost-data" element={<CostDataPage />} />
      <Route path="/cost-data/:finishedBatchId" element={<RecordDetailPage />} />
      <Route path="/ai" element={<AiWorkspacePage />} />
      <Route path="/ai/:conversationId" element={<AiWorkspacePage />} />
      <Route path="/reports" element={<ReportsPage />} />
      <Route path="/reports/:artifactId" element={<ReportsPage />} />
      <Route path="/data-admin" element={<DataAdminPage />} />
      <Route path="/data-admin/:batchId" element={<DataAdminPage />} />
      {/* ``new`` 必须先于 ``:jobId`` 声明，否则会被动态段吞掉。 */}
      <Route path="/data-admin/imports" element={<ImportJobsPage />} />
      <Route path="/data-admin/imports/new" element={<GovernanceSubmitPage />} />
      <Route path="/data-admin/imports/:jobId" element={<ImportJobDetailPage />} />
      <Route path="/iam" element={<IamPage />} />
      <Route path="/audit" element={<AuditPage />} />
    </Route>
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes></Suspense>
}
