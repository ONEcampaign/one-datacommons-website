/**
 * Copyright 2025 Google LLC
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

/**
 * One.org: A component that renders the header on all pages via the base template.
 */

import React, { ReactElement } from "react";

import { useBreakpoints } from "../../../../../../shared/hooks/breakpoints";
import HeaderBarSearch from "../../../../../base/components/header_bar/header_bar_search";
import HeaderLogo from "./header_logo";
import MenuDesktop from "./menu_desktop";
import MenuMobile from "./menu_mobile";

interface HeaderBarProps {
  //if set true, the search bar will operate in "hash mode", changing the hash rather than redirecting.
  searchBarHashMode: boolean;
  //the root of the primary data.one.org site
  primarySiteWebRoot: string;
}

const HeaderBar = ({
  searchBarHashMode,
  primarySiteWebRoot,
}: HeaderBarProps): ReactElement => {
  const { up, down } = useBreakpoints();

  return (
    <div id="main-header-container">
      <nav id="main-navbar-container">
        <div className="navbar-menu-large">
          <HeaderLogo />
          {up("lg") && (
            <HeaderBarSearch searchBarHashMode={searchBarHashMode} />
          )}
          <MenuDesktop primarySiteWebRoot={primarySiteWebRoot} />
        </div>
        <div className="navbar-menu-mobile">
          <HeaderLogo />
          {down("md") && (
            <HeaderBarSearch searchBarHashMode={searchBarHashMode} />
          )}
          <MenuMobile primarySiteWebRoot={primarySiteWebRoot} />
        </div>
      </nav>
    </div>
  );
};

export default HeaderBar;
