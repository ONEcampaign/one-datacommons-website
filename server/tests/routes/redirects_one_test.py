import unittest

from flask import Flask

from server.routes import redirects_one


def _make_app():
  app = Flask(__name__)

  @app.route('/api/place/name')
  def api_place():
    return 'api-ok'

  @app.route('/api/observations/point')
  def api_obs():
    return 'api-ok'

  @app.route('/core/api/v2/observation', methods=['GET', 'POST'])
  def core_obs():
    return 'core-ok'

  @app.route('/core/api/v2/node', methods=['GET', 'POST'])
  def core_node():
    return 'core-ok'

  @app.route('/healthz')
  def healthz():
    return 'ok'

  redirects_one.install(app)
  return app


class TestRedirectsOne(unittest.TestCase):

  def setUp(self):
    self.client = _make_app().test_client()

  def _assert_redirect(self, path, target='https://data.one.org/tools'):
    resp = self.client.get(path, follow_redirects=False)
    self.assertEqual(resp.status_code, 301, f'{path} should 301')
    self.assertEqual(resp.headers['Location'], target,
                     f'{path} redirected to wrong target')

  def _assert_passthrough(self, path):
    resp = self.client.get(path, follow_redirects=False)
    self.assertLess(resp.status_code, 300, f'{path} should not redirect')

  def test_root_redirects_to_homepage(self):
    self._assert_redirect('/', target='https://data.one.org')

  def test_scatter_redirects(self):
    self._assert_redirect('/tools/scatter')

  def test_timeline_redirects(self):
    self._assert_redirect('/tools/timeline')

  def test_map_redirects(self):
    self._assert_redirect('/tools/map')

  def test_places_redirects(self):
    self._assert_redirect('/places')

  def test_download_redirects(self):
    self._assert_redirect('/tools/download')

  def test_explore_redirects(self):
    self._assert_redirect('/explore')

  def test_place_redirects(self):
    self._assert_redirect('/place/country/USA')

  def test_browser_redirects(self):
    self._assert_redirect('/browser')

  def test_browser_dcid_redirects(self):
    self._assert_redirect('/browser/geoId/06')

  def test_ranking_redirects(self):
    self._assert_redirect('/ranking/Count_Person/State')

  def test_topic_redirects(self):
    self._assert_redirect('/topic/health')

  def test_factcheck_redirects(self):
    self._assert_redirect('/factcheck')

  def test_disasters_redirects(self):
    self._assert_redirect('/disasters')

  def test_search_redirects(self):
    self._assert_redirect('/search')

  def test_about_redirects(self):
    self._assert_redirect('/about')

  def test_data_redirects(self):
    self._assert_redirect('/data')

  def test_faq_redirects(self):
    self._assert_redirect('/faq')

  def test_robots_redirects(self):
    self._assert_redirect('/robots.txt')

  def test_random_path_redirects(self):
    self._assert_redirect('/some/random/unknown/path')

  def test_trailing_slash_redirects(self):
    self._assert_redirect('/tools/scatter/')

  def test_query_string_redirects(self):
    resp = self.client.get('/tools/map?statVar=Count_Person',
                           follow_redirects=False)
    self.assertEqual(resp.status_code, 301)
    self.assertEqual(resp.headers['Location'], 'https://data.one.org/tools')

  def test_api_place_passes_through(self):
    self._assert_passthrough('/api/place/name')

  def test_api_observations_passes_through(self):
    self._assert_passthrough('/api/observations/point')

  def test_core_api_get_passes_through(self):
    self._assert_passthrough('/core/api/v2/observation')

  def test_core_api_post_passes_through(self):
    resp = self.client.post('/core/api/v2/node', follow_redirects=False)
    self.assertLess(resp.status_code, 300)

  def test_healthz_passes_through(self):
    self._assert_passthrough('/healthz')

  def test_should_redirect_helper(self):
    redirects = [
        '/', '/tools/scatter', '/explore', '/place/country/USA', '/browser',
        '/ranking/foo/bar', '/about', '/faq', '/robots.txt', '/anything'
    ]
    passthroughs = [
        '/api/place/name', '/api/observations/point',
        '/core/api/v2/observation', '/core/api/v2/node', '/healthz'
    ]
    for p in redirects:
      self.assertTrue(redirects_one.should_redirect(p), p)
    for p in passthroughs:
      self.assertFalse(redirects_one.should_redirect(p), p)


if __name__ == '__main__':
  unittest.main()
