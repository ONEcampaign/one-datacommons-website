export interface MenuSource {
  id: string;
  title: string;
  url: string;
  description: string;
  childContent?: Array<{ title: string; url: string; description?: string }>;
}

export const MenuItems: MenuSource[] = [
  {
    id: "analysis",
    title: "Analysis",
    url: "{primaryWebRoot}/analysis",
    description:
      "Smart **articles** on the economic, political, and social forces shaping the world – and what they mean for Africa and beyond.",
    childContent: [
      {
        title: "Deep Dives",
        url: "{primaryWebRoot}/analysis/type/deep-dive",
        description:
          "In-depth analyses and comprehensive explorations of key topics.",
      },
      {
        title: "Overviews",
        url: "{primaryWebRoot}/analysis/type/overview",
        description:
          "Get the essentials—background, key insights, and framing of major issues.",
      },
      {
        title: "Advocacy",
        url: "{primaryWebRoot}/analysis/type/advocacy",
        description:
          "Actionable insights, policy recommendations, and timely perspectives.",
      },
      {
        title: "All our analysis",
        url: "{primaryWebRoot}/analysis",
        description: "Browse all our articles.",
      },
    ],
  },
  {
    id: "data",
    title: "Data",
    url: "{primaryWebRoot}/places",
    description:
      "Use our **interactive tools**, powered by Google Data Commons, to explore and analyse millions of development data points seamlessly.",
    childContent: [
      {
        title: "Place explorer",
        url: "{primaryWebRoot}/places",
      },
      {
        title: "Scatter plots",
        url: "/tools/scatter",
      },
      {
        title: "Timelines",
        url: "/tools/timeline",
      },
      {
        title: "Map explorer",
        url: "/tools/map",
      },
      {
        title: "Data downloads",
        url: "/tools/downloads",
      },
    ],
  },
  {
    id: "about-us",
    title: "About Us",
    url: "{primaryWebRoot}/about",
    description:
      "We provide cutting-edge data, analysis, and tools so that together we can fight for a more just world.",
    childContent: [
      {
        title: "About Us",
        url: "{primaryWebRoot}/about",
      },
      {
        title: "Our Team",
        url: "{primaryWebRoot}/about/team",
      },
      {
        title: "FAQ",
        url: "{primaryWebRoot}/about/faq",
      },
      {
        title: "Newsletter",
        url: "{primaryWebRoot}/newsletter",
      },
    ],
  },
];

export function prepareMenu(
  menuItems: MenuSource[],
  primaryWebRoot: string
): MenuSource[] {
  return menuItems.map((item) => ({
    ...item,
    url: item.url.replace("{primaryWebRoot}", primaryWebRoot),
    childContent: item.childContent?.map((child) => ({
      ...child,
      url: child.url.replace("{primaryWebRoot}", primaryWebRoot),
    })),
  }));
}
