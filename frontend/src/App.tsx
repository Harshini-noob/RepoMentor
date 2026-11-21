import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Dashboard } from "./pages/Dashboard";
import { DossierPage } from "./pages/DossierPage";
import { ChatPage } from "./pages/ChatPage";
import { InterviewPage } from "./pages/InterviewPage";
import { ProgressPage } from "./pages/ProgressPage";

export const App: React.FC = () => {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/projects/:id/dossier" element={<DossierPage />} />
          <Route path="/projects/:id/chat" element={<ChatPage />} />
          <Route path="/projects/:id/interview" element={<InterviewPage />} />
          <Route path="/projects/:id/progress" element={<ProgressPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
};

export default App;
