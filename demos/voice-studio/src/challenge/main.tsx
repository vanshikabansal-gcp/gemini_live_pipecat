import { createRoot } from "react-dom/client";
import ChallengeApp from "./challenge-app.tsx";
import "./challenge.css";

createRoot(document.getElementById("root")!).render(<ChallengeApp />);
