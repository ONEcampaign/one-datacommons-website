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
 * One.org: A component to display the footer
 */

import React, { ReactElement } from "react";

import { One } from "../../components/elements/icons/one";
import { SocialFacebook } from "../../components/elements/icons/social_facebook";
import { SocialInstagram } from "../../components/elements/icons/social_instagram";
import { SocialX } from "../../components/elements/icons/social_x";
import { SocialYouTube } from "../../components/elements/icons/social_youtube";

interface FooterProps {
  //the root of the primary data.one.org site
  primarySiteWebRoot: string;
}

export const Footer = ({ primarySiteWebRoot }: FooterProps): ReactElement => {
  return (
    <section id="footer">
      <footer className="footer">
        <div className="container">
          <div className="footer-grid">
            <div className="footer-brand">
              <div className="footer-logo">
                <One width={80} />
                <span>Data</span>
              </div>
              <p className="footer-description">
                ONE Data provides insights on global challenges through data,
                analysis, and tools to drive action toward a more just world.
                Powered by Data Commons.
              </p>
            </div>

            <div className="footer-links">
              <div>
                <h2>Our Work</h2>
                <ul>
                  <li>
                    <a href={`${primarySiteWebRoot}/analysis`}>Analysis</a>
                  </li>
                  <li>
                    <a href="/">Data</a>
                  </li>
                  <li>
                    <a href={`${primarySiteWebRoot}/newsletter`}>Newsletter</a>
                  </li>
                </ul>
              </div>
              <div>
                <h2>About Us</h2>
                <ul>
                  <li>
                    <a href={`${primarySiteWebRoot}/about`}>About Us</a>
                  </li>
                  <li>
                    <a href={`${primarySiteWebRoot}/about/team`}>Our Team</a>
                  </li>
                  <li>
                    <a href={`${primarySiteWebRoot}/about/faq`}>FAQ</a>
                  </li>
                </ul>
              </div>
              <div>
                <h2>Connect with Us</h2>
                <ul className="social-links">
                  <li>
                    <a href="https://www.facebook.com/ONE">
                      <SocialFacebook />
                    </a>
                  </li>
                  <li>
                    <a href="https://instagram.com/one">
                      <SocialInstagram />
                    </a>
                  </li>
                  <li>
                    <a href="https://x.com/ONEAftershocks">
                      <SocialX />
                    </a>
                  </li>
                  <li>
                    <a href="https://youtube.com/">
                      <SocialYouTube />
                    </a>
                  </li>
                </ul>
              </div>
            </div>
          </div>

          <div className="footer-bottom">
            <p>
              <strong>© 2025 ONE Campaign.</strong> All rights reserved.
            </p>
            <ul className="footer-bottom-links">
              <li>
                <a href={`${primarySiteWebRoot}/sitemap`}>Sitemap</a>
              </li>
            </ul>
          </div>
        </div>
      </footer>
    </section>
  );
};
