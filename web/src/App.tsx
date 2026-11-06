import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Folders from "./pages/Folders";
import GroupDetail from "./pages/GroupDetail";
import Groups from "./pages/Groups";
import Home from "./pages/Home";
import Jobs from "./pages/Jobs";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Profiles from "./pages/Profiles";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<Layout />}>
          <Route path="/" element={<Home />} />
          <Route path="/profiles" element={<Profiles />} />
          <Route path="/p/:name/overview" element={<Overview />} />
          <Route path="/p/:name/folders" element={<Folders />} />
          <Route path="/p/:name/groups" element={<Groups />} />
          <Route path="/p/:name/groups/:groupId" element={<GroupDetail />} />
          <Route path="/p/:name/jobs" element={<Jobs />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
