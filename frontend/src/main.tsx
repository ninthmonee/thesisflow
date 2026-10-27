import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./app/app";
import "./app/global.css";

const root = document.getElementById("root");
if (!root) throw new Error("Application root is missing");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
