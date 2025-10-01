module.exports = {
  entry: {
    base: [__dirname + "/js/apps/custom_dc/one/base/main.ts", __dirname + "/css/custom_dc/one/core.scss"],
    homepage_custom_dc: null,
    homepage: [
      __dirname + "/js/apps/custom_dc/one/homepage/main.ts",
      __dirname + "/css/custom_dc/one/homepage.scss",
    ],
  },
  resolve: {
    alias: {
      'theme': 'js/theme/dc_custom_theme',
      'js/components/nl_search_bar/auto_complete_input': __dirname + "/js/apps/custom_dc/one/base/components/nl_search_bar/auto_complete_input.tsx",
    },
  },
};