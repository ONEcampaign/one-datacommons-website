/**
 * Copyright 2024 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

<<<<<<<< HEAD:static/css/biomedical/_biomedical_colors.scss
/** Colors for the Biomedical DC Site
 *
 * Overrides the colors set in css/base/_color.scss 
 * Provides theming for styled components
 */

:root,
:host {
  --main-content-background-color: #f9fdfd;
  --text-link-color: #146c2e;
  // theming of table components
  --table-link-color: var(--text-link-color);
  --table-text-color: #212529;
  // theming of button form components
  --button-background-color: var(--main-content-background-color);
  --button-highlight-background-color: #effaf5;
  --button-text-color: var(--text-link-color);
========
/**
 * Entry point for the about your own Data Commons page.
 */

import React from "react";
import ReactDOM from "react-dom";

import { loadLocaleData } from "../../i18n/i18n";
import { extractRoutes } from "../base/utilities/utilities";
import { App } from "./app";

window.addEventListener("load", (): void => {
  loadLocaleData("en", [import("../../i18n/compiled-lang/en/units.json")]).then(
    () => {
      renderPage();
    }
  );
});

function renderPage(): void {
  const routes = extractRoutes();

  ReactDOM.render(
    React.createElement(App, { routes }),
    document.getElementById("app-container")
  );
>>>>>>>> staging:static/js/apps/about/main.ts
}
