"""Suite de tests cote serveur.

    py tests/server_tests.py            # hors ligne, quelques secondes
    py tests/server_tests.py --online   # ajoute ceux qui sortent sur le reseau

Deux principes, nes chacun d'un incident :

* **Zero assertion est un echec, pas un succes.** Un compteur qui tombe a zero
  sans que rien n'echoue est le pire des deux mondes : la suite a paru verte
  alors qu'un test ne recevait plus ses arguments et ne mesurait plus rien.
* **Un test sans run de controle ne prouve rien.** Les sections d'annulation
  lancent chacune deux fois le meme code, une fois avec le mecanisme et une fois
  sans. Si les deux passent, le test ne mesure pas ce qu'il pretend mesurer.

Les monkeypatches sont restaures apres chaque section : ils portent sur le
module `app` lui-meme, donc sans cela l'ordre des tests deviendrait porteur.
"""
import contextlib
import io
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app  # noqa: E402  (le serveur est le sujet du test)

ONLINE = '--online' in sys.argv
# app.PORT est la seule source du numero de port dans tout le projet. Le
# recopier ici ferait passer la suite au rouge en 403 au premier changement,
# pour une raison etrangere a ce qu'elle teste.
H = {'Host': '127.0.0.1:%d' % app.PORT}
BS = '\\'

_resultats = []


def section(titre):
    print('\n--- %s ---' % titre)


def premiere_trame(reponse):
    """Premiere trame SSE, en texte et refermable.

    `Response.response` est typee Iterable : ni `.close()` ni un test `in`
    n'y sont accessibles statiquement, et Flask peut rendre des bytes.
    """
    gen: Any = reponse
    trame = next(iter(gen))
    if isinstance(trame, bytes):
        trame = trame.decode('utf-8', 'replace')
    return trame, gen


def check(nom, ok, detail=''):
    _resultats.append(bool(ok))
    print('  [%s] %s %s' % ('OK  ' if ok else 'ECHEC', nom.ljust(46), detail))


@contextlib.contextmanager
def patched_on(cible, **remplacements):
    """Remplace des attributs puis les restaure.

    Sans restauration, un faux yt-dlp pose par une section resterait en place
    pour toutes les suivantes, et l'ordre du fichier deviendrait un contrat
    tacite que personne ne lirait.
    """
    anciens = {k: getattr(cible, k) for k in remplacements}
    for k, v in remplacements.items():
        setattr(cible, k, v)
    try:
        yield
    finally:
        for k, v in anciens.items():
            setattr(cible, k, v)


def patched(**remplacements):
    return patched_on(app, **remplacements)


def fake_ydl(classe):
    return patched_on(app.yt_dlp, YoutubeDL=classe)


def faux_ydl(extraire, journal=None):
    """Fabrique une classe yt_dlp.YoutubeDL de test.

    Les trois sections qui en avaient besoin reecrivaient le meme
    __init__/__enter__/__exit__ ; seul extract_info differe. `journal` recoit
    les options de chaque instanciation, ce qui suffit a savoir ce qui a ete
    demande a yt-dlp.
    """
    class Faux:
        def __init__(self, opts):
            if journal is not None:
                journal.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, url, download=True):
            return extraire(url, download)

    return Faux


client = app.app.test_client()


# ===================================================== garde d'origine
def test_origine():
    section("Garde d'origine")
    cas = [
        ("Host legitime 127.0.0.1", {'Host': '127.0.0.1:%d' % app.PORT}, 'autorise'),
        ("Host legitime localhost", {'Host': 'localhost:%d' % app.PORT}, 'autorise'),
        ("Host attaquant (rebinding DNS)", {'Host': 'evil.example:%d' % app.PORT}, 'refuse'),
        ("Origin tiers", {**H, 'Origin': 'https://evil.example'}, 'refuse'),
        ("Origin legitime", {**H, 'Origin': 'http://localhost:%d' % app.PORT}, 'autorise'),
    ]
    for nom, headers, attendu in cas:
        r = client.get('/list-downloads', headers=headers)
        obtenu = 'refuse' if r.status_code == 403 else 'autorise'
        check(nom, obtenu == attendu, '-> %s %s (attendu %s)' % (r.status_code, obtenu, attendu))

    # En multipart, cette route echappait a la protection CORS accidentelle
    # dont beneficiaient les autres.
    r = client.post('/upload-for-cut', headers={**H, 'Origin': 'https://evil.example'},
                    data={'file': (io.BytesIO(b'x'), 'x.mp4')},
                    content_type='multipart/form-data')
    check('upload multipart cross-origin refuse', r.status_code == 403, '-> %s' % r.status_code)


def test_sec_fetch_site():
    section('Repli sur Sec-Fetch-Site, et en-tetes de securite')
    # Les navigateurs n'envoient pas Origin sur un GET no-cors : un <video src>
    # place sur un site tiers traversait la garde et servait d'oracle sur les
    # fichiers presents.
    cas = [
        ("navigation pywebview (none)", 'none', 'autorise'),
        ("sous-ressource same-origin", 'same-origin', 'autorise'),
        ("<video> cross-site (l'oracle)", 'cross-site', 'refuse'),
        ("same-site, autre origine", 'same-site', 'refuse'),
    ]
    for nom, sfs, attendu in cas:
        r = client.get('/stream/Videos/x.mp4', headers={**H, 'Sec-Fetch-Site': sfs})
        obtenu = 'refuse' if r.status_code == 403 else 'autorise'
        check(nom, obtenu == attendu, '-> %s %s (attendu %s)' % (r.status_code, obtenu, attendu))

    r = client.get('/stream/Videos/x.mp4', headers=H)
    check('client sans Sec-Fetch (curl) autorise', r.status_code != 403, '-> %s' % r.status_code)

    r = client.get('/', headers={**H, 'Sec-Fetch-Site': 'none'})
    xfo = r.headers.get('X-Frame-Options')
    csp = r.headers.get('Content-Security-Policy', '')
    check('X-Frame-Options: DENY', xfo == 'DENY', '-> %s' % xfo)
    check('CSP frame-ancestors', "frame-ancestors 'none'" in csp, csp[:60])


# ===================================================== securite des paths
def test_sanitize():
    section('sanitize_filename et confinement')
    sf = app.sanitize_filename
    cas = [
        # Les '..' internes ne doivent PAS etre mutiles : prives de separateur
        # ils ne designent plus un parent, et les neutraliser cassait des noms
        # legitimes, listes puis introuvables a la lecture.
        ('nom legitime a points', 'Wait... What.mp4', 'Wait... What.mp4'),
        ('nom legitime simple', 'Video 2024.mp4', 'Video 2024.mp4'),
        ('traversal unix', '../../etc/passwd', None),
        ('traversal windows', '..' + BS + '..' + BS + 'a.exe', None),
        ('nom vide apres nettoyage', '..', '_'),
    ]
    for nom, entree, attendu in cas:
        got = sf(entree)
        if attendu is None:
            ok, detail = ('/' not in got and BS not in got), 'aucun separateur ne survit'
        else:
            ok, detail = (got == attendu), 'attendu %r' % attendu
        check(nom, ok, '%r -> %r (%s)' % (entree, got, detail))

    folder = app.VIDEOS_FOLDER.resolve()
    for entree in ['../../etc/passwd', '..' + BS + '..' + BS + 'a.exe', 'Wait... What.mp4']:
        p = (folder / sf(entree)).resolve()
        check('confinement %s' % repr(entree)[:26], str(p).startswith(str(folder)),
              'reste dans Videos/')


# ===================================================== comportements de base
def test_comportements():
    section('File saturee, balayage, format, fuite SSE')
    # Un put_nowait nu levait queue.Full, l'exception remontait dans le
    # gestionnaire generique et declenchait a tort le repli photo Instagram.
    q = queue.Queue(maxsize=3)
    for i in range(3):
        q.put_nowait({'status': 'downloading', 'n': i})
    try:
        app._put_final(q, {'status': 'complete', 'filename': 'ok.mp4'})
        vides = []
        while not q.empty():
            vides.append(q.get_nowait())
        check('evenement terminal sur file pleine',
              any(e.get('status') == 'complete' for e in vides), 'complete transmis')
    except queue.Full:
        check('evenement terminal sur file pleine', False, "queue.Full levee (le bug d'origine)")

    dossier = Path(tempfile.mkdtemp(prefix='sweep-'))
    recent, ancien = dossier / 'recent.tmp', dossier / 'vieux.tmp'
    recent.write_bytes(b'x')
    ancien.write_bytes(b'x')
    os.utime(ancien, (time.time() - 9999, time.time() - 9999))
    app._sweep_old_files(dossier, 300)
    check('balayage par age', recent.exists() and not ancien.exists(),
          'recent garde, ancien supprime')
    shutil.rmtree(dossier, ignore_errors=True)

    # merge_output_format et le convertisseur MP4 ne sont poses que pour
    # youtube/playlist : un bestvideo+bestaudio ici sortirait un .mkv illisible.
    fs = app._get_format_string('social', 480)
    check('qualite honoree pour social', 'height<=480' in fs, fs[:56])

    # Sans le finally du generateur, une deconnexion client laissait l'entree
    # et sa queue en memoire definitivement.
    app.download_progress['zz'] = {'queue': queue.Queue(maxsize=10),
                                   'start_time': time.time(),
                                   'deadline': time.time() + 1800}
    app.download_progress['zz']['queue'].put_nowait({'status': 'downloading', 'percent': 1})
    with app.app.test_request_context('/progress/zz', headers=H):
        _, gen = premiere_trame(app.progress_stream('zz').response)
        gen.close()
    check('fuite SSE a la deconnexion', 'zz' not in app.download_progress, 'entree retiree')


# ===================================================== echeances
def test_echeances():
    section("L'echeance appartient au job")
    debut = time.time() - 3000
    entry = {'queue': queue.Queue(maxsize=10), 'start_time': debut,
             'deadline': debut + 1800, 'cancelled': False}
    app.download_progress['pl'] = entry
    hook = app._make_abort_hook(entry)

    leve = False
    try:
        hook({})
    except app.DownloadAborted as e:
        leve = (e.message == app.TIMEOUT_MSG)
    check('echeance depassee : le hook leve', leve)

    # La boucle playlist repousse video par video : une playlist longue est un
    # usage legitime. Quand le filet SSE etait ancre sur start_time, il coupait
    # le client a 31 min pendant que le worker continuait.
    entry['deadline'] = time.time() + app.DOWNLOAD_TIMEOUT
    # except Exception et non DownloadAborted : filtrer sur le message laissait
    # passer un arret d'un autre genre, et l'assertion restait verte.
    leve_encore = None
    try:
        hook({})
    except Exception as e:
        leve_encore = type(e).__name__
    check('echeance repoussee : le hook se tait', leve_encore is None,
          'leve : %s' % leve_encore)

    entry['queue'].put_nowait({'status': 'downloading', 'percent': 50})
    with app.app.test_request_context('/progress/pl', headers=H):
        trame, _ = premiere_trame(app.progress_stream('pl').response)
    check("le SSE suit l'echeance, pas start_time", 'Timeout' not in trame, trame.strip()[:50])
    check('message derive de la constante', str(app.DOWNLOAD_TIMEOUT // 60) in app.TIMEOUT_MSG,
          app.TIMEOUT_MSG)


def test_playlist_repousse():
    section("La boucle playlist repousse l'echeance")

    def extraire(url, download):
        if not download:
            return {'title': 'PL',
                    'entries': [{'id': 'v%d' % i, 'title': 'V%d' % i} for i in range(3)]}
        return {'title': 'ok'}

    debut = time.time() - 3000
    entry = {'queue': queue.Queue(maxsize=100), 'start_time': debut,
             'deadline': debut + app.DOWNLOAD_TIMEOUT, 'cancelled': False}
    with fake_ydl(faux_ydl(extraire)):
        app._run_playlist_download(entry['queue'], 'http://x/pl', None, entry, set())
    check("echeance du registre repoussee", entry['deadline'] > time.time(),
          '%+d s' % (entry['deadline'] - time.time()))

    app.download_progress['pl2'] = entry
    entry['queue'].put_nowait({'status': 'downloading', 'percent': 10})
    with app.app.test_request_context('/progress/pl2', headers=H):
        trame, _ = premiere_trame(app.progress_stream('pl2').response)
    check('le SSE ne coupe pas le client', 'Timeout' not in trame, trame.strip()[:50])


# ===================================================== routage des URL
RADIO = 'https://www.youtube.com/watch?v=MEKERDMnC48&list=RDMEKERDMnC48&start_radio=1'


def test_routage():
    section('Routage playlist / video, et noplaylist')
    appels = []

    def extraire(url, download):
        if appels[-1].get('extract_flat'):
            # 4 entrees rendues, 1593 annoncees : la forme exacte que produit
            # playlistend, la borne posee sur l'enumeration.
            return {'title': 'Ma Playlist', 'uploader': 'Moi',
                    'playlist_count': 1593,
                    'entries': [{'title': 'V%d' % i, 'duration': 10, 'url': 'u%d' % i}
                                for i in range(4)]}
        return {'title': 'Une video', 'formats': [], 'duration': 12,
                'extractor_key': 'Youtube'}

    with fake_ydl(faux_ydl(extraire, journal=appels)):
        appels.clear()
        d = client.post('/get-info', headers=H, json={
            'url': 'https://www.youtube.com/playlist?list=PLabc'}).get_json()
        check('playlist reconnue par le serveur', d.get('is_playlist') is True)
        check('extraction plate utilisee', any(o.get('extract_flat') for o in appels))
        # Le compte vient de playlist_count, pas de la longueur enumeree :
        # sans cela, borner l'apercu aurait fait mentir l'affichage.
        check('compte total, pas la longueur tronquee', d.get('video_count') == 1593,
              str(d.get('video_count')))
        check('aucune liste de videos dans la reponse', 'videos' not in d,
              'la charge n\'etait lue par personne')
        check("l'enumeration est bornee",
              any(o.get('playlistend') == app.PLAYLIST_SCAN for o in appels),
              'playlistend=%s' % [o.get('playlistend') for o in appels])
        check('playlist : noplaylist absent', not any(o.get('noplaylist') for o in appels))

        appels.clear()
        d2 = client.post('/get-info', headers=H, json={
            'url': 'https://www.youtube.com/watch?v=abc12345678'}).get_json()
        check("video simple : pas d'extraction plate",
              not any(o.get('extract_flat') for o in appels))
        check('video simple : is_playlist faux', d2.get('is_playlist') is False)
        check('video simple : noplaylist transmis',
              bool(appels) and all(o.get('noplaylist') for o in appels),
              '%d appel(s)' % len(appels))

        # Le lien rapporte : une radio YouTube. is_playlist_url dit deja « une
        # video », mais sans noplaylist yt-dlp deroulait la radio, qui n'a pas
        # de fin -- mesure, plus de 100 s sans reponse contre 1 s avec.
        appels.clear()
        d3 = client.post('/get-info', headers=H, json={'url': RADIO}).get_json()
        check('radio : traitee comme une video', d3.get('is_playlist') is False)
        check("radio : pas d'extraction plate", not any(o.get('extract_flat') for o in appels))
        check('radio : noplaylist transmis',
              bool(appels) and all(o.get('noplaylist') for o in appels),
              '%d appel(s)' % len(appels))

    # 'mp3' et pas seulement 'youtube' : c'est le type ou l'option porte
    # vraiment. Pour youtube/playlist, start_download passe par
    # clean_youtube_url, qui retire deja le '&list='.
    for t in ('mp3', 'youtube', 'social'):
        o = app._build_ydl_opts(t, RADIO, None, lambda d: None,
                                {'deadline': 9e9, 'cancelled': False}, 'C:' + BS + 'tmp')
        check("worker '%s' : noplaylist pose" % t, o.get('noplaylist') is True)

    check('extractions plates : noplaylist absent',
          'noplaylist' not in app._flat_ydl_opts(), str(sorted(app._flat_ydl_opts())))
    # Le telechargement a besoin de la liste ENTIERE : la borne ne doit exister
    # que la ou on la demande explicitement.
    check('les options plates ne bornent rien', 'playlistend' not in app._flat_ydl_opts(),
          "c'est l'apercu qui la pose, pas le telechargement")
    check('clean_youtube_url retire le list=', 'list=' not in app.clean_youtube_url(RADIO),
          app.clean_youtube_url(RADIO))

    r = client.post('/get-playlist-info', headers=H, json={})
    check('ancien endpoint retire', r.status_code == 404, 'code=%s' % r.status_code)


# ===================================================== photos Instagram
def test_photo_sans_audio():
    section('Un post photo ne doit pas proposer de MP3')
    appels = []

    def extraire(url, download):
        # Extraction reussie mais sans aucun format : c'est le cas reel d'un
        # post photo, et c'est cette meme branche qui met _is_photo a vrai.
        return {'title': 'Une photo', 'formats': [], 'duration': 0,
                'extractor_key': 'Instagram'}

    with fake_ydl(faux_ydl(extraire, journal=appels)):
        d = client.post('/get-info', headers=H,
                        json={'url': 'https://www.instagram.com/p/ABC/'}).get_json() or {}
    check('reconnu comme une photo', d.get('_is_photo') is True, str(d.get('_is_photo')))
    # has_audio valait True par defaut « faute de formats » -- la branche meme
    # qui rend _is_photo vrai. Le client posait donc un bouton MP3 apres le
    # bouton Photo, et c'est le MP3 qui finissait selectionne.
    check("aucune piste audio annoncee", d.get('has_audio') is False,
          'has_audio = %s' % d.get('has_audio'))
    check('aucune qualite video', not d.get('available_qualities'))


def test_playlist_compte_partiel():
    section('Compte de playlist : plancher annonce comme tel')
    for playlist_count, entrees, attendu_partiel in ((1593, 4, False), (None, 60, True)):
        appels = []

        def extraire(url, download, pc=playlist_count, n=entrees):
            info = {'title': 'PL', 'uploader': 'Moi',
                    'entries': [{'title': 'V%d' % i, 'url': 'u%d' % i} for i in range(n)]}
            if pc:
                info['playlist_count'] = pc
            return info

        with fake_ydl(faux_ydl(extraire, journal=appels)):
            d = client.post('/get-info', headers=H, json={
                'url': 'https://www.youtube.com/playlist?list=PLx'}).get_json() or {}
        # Sans playlist_count, le repli est le nombre ENUMERE, lui-meme plafonne
        # par playlistend : annoncer « 50 » pour 3000 videos serait un mensonge
        # muet, d'ou le drapeau que le client rend en « 50+ ».
        check('partiel = %s (playlist_count=%s)' % (attendu_partiel, playlist_count),
              d.get('video_count_partial') is attendu_partiel,
              'video_count=%s partiel=%s' % (d.get('video_count'), d.get('video_count_partial')))


def test_photos():
    section('Photos Instagram : strategie choisie en amont')
    # Sans ce patch, la route lance un vrai thread qui appelle l'API Instagram
    # avec les cookies presents dans BASE_DIR, et un succes ecrirait dans le
    # vrai downloads/Photos/. Le test etait vert par accident de configuration.
    with patched(_download_instagram_images=lambda *a, **k: None):
        r = client.post('/start-download', headers=H,
                        json={'url': 'https://www.instagram.com/p/ABC/', 'type': 'photo'})
        check('photo acceptee sur Instagram', r.status_code == 200, 'code=%s' % r.status_code)
        r = client.post('/start-download', headers=H,
                        json={'url': 'https://www.tiktok.com/@x/video/1', 'type': 'photo'})
        check('photo refusee ailleurs', r.status_code == 400, 'code=%s' % r.status_code)

    vus = {}

    instanciations = []

    def refuse(url, download):
        raise RuntimeError('yt-dlp ne doit pas etre appele')

    # entry en positionnel obligatoire, sans defaut : c'est lui qui porte le
    # drapeau d'annulation. Un defaut laisserait passer un appelant qui
    # l'oublie, et l'arret des photos redeviendrait inerte sans rien casser.
    def fausses_images(q, url, entry):
        vus['img'] = True
        vus['entry'] = entry
        app._put_final(q, {'status': 'complete', 'title': 'ok', 'filename': 'x.jpg'})

    q = queue.Queue(maxsize=100)
    now = time.time()
    app.download_progress['t1'] = {'queue': q, 'start_time': now, 'deadline': now + 1800,
                                   'cancelled': False}
    with fake_ydl(faux_ydl(refuse, journal=instanciations)), \
            patched(_download_instagram_images=fausses_images):
        app._run_download('t1', 'https://www.instagram.com/p/ABC/', 'photo')

    check('yt-dlp non sollicite', not instanciations,
          '%d instanciation(s)' % len(instanciations))
    check("telechargeur d'images appele directement", vus.get('img') is True)
    ev = []
    while not q.empty():
        ev.append(q.get_nowait())
    check('evenement terminal emis', any(e.get('status') == 'complete' for e in ev))
    check("l'entree du registre est transmise",
          vus.get('entry') is app.download_progress.get('t1'))


# ===================================================== annulation
def test_route_cancel():
    section('Route /cancel')
    now = time.time()
    app.download_progress['dl1'] = {'queue': queue.Queue(maxsize=10), 'start_time': now,
                                    'deadline': now + 1800, 'cancelled': False}
    r = client.post('/cancel/dl1', headers=H)
    check("annulation d'un telechargement",
          r.status_code == 200 and app.download_progress['dl1']['cancelled'])

    class FauxProc:
        def __init__(self):
            self.tue = False

        def kill(self):
            self.tue = True

    proc = FauxProc()
    app.cut_progress['cut1'] = {'queue': queue.Queue(maxsize=10), 'cancelled': False,
                                'proc': proc}
    r = client.post('/cancel/cut1', headers=H)
    check("annulation d'une decoupe : le process est tue",
          r.status_code == 200 and proc.tue and app.cut_progress['cut1']['cancelled'])

    r = client.post('/cancel/inexistant', headers=H)
    d = r.get_json()
    check('job inconnu : succes, sans erreur',
          r.status_code == 200 and d.get('success') is True, str(d))

    hook = app._make_abort_hook(app.download_progress['dl1'])
    leve = None
    try:
        hook({})
    except app.DownloadAborted as e:
        leve = e.status
    # On asserte sur le status publie, pas sur le nom de la classe : c'est le
    # status que le client voit, et c'est lui qui doit rester stable.
    check("le hook demande un arret 'cancelled'", leve == 'cancelled', 'leve : %s' % leve)

    app.download_progress['dl1']['cancelled'] = False
    rien = None
    try:
        hook({})
    except Exception as e:
        rien = type(e).__name__
    check('sans drapeau : le hook se tait', rien is None, 'leve : %s' % rien)


def _instagram_continue(annuler):
    """Un run de la boucle photos ou TOUTES les conversions echouent.

    C'est le chemin `continue`, qui sautait entierement la verification quand
    elle etait en fin de corps de boucle.
    """
    NB = 5
    scratch = Path(tempfile.mkdtemp(prefix='ig-'))
    cookies = scratch / 'cookies.txt'
    cookies.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
    entry = {'queue': queue.Queue(maxsize=100), 'cancelled': False}
    gets = []

    class Resp:
        def __init__(self, payload=None):
            self.status_code = 200
            self._p = payload
            self.content = b'\xff\xd8\xff\xe0 fausses donnees'

        def json(self):
            return self._p

        def raise_for_status(self):
            pass

    class Session:
        def __init__(self):
            self.headers = {}
            self.cookies = None

        def get(self, url, timeout=None):
            assert timeout and timeout > 0, 'appel sans timeout'
            gets.append(url)
            if '/api/v1/' in url:
                media = [{'image_versions2': {'candidates': [{'url': 'https://img/%d' % i}]}}
                         for i in range(NB)]
                return Resp({'items': [{'carousel_media': media}]})
            if annuler and len([g for g in gets if g.startswith('https://img/')]) >= 2:
                entry['cancelled'] = True
            return Resp()

    faux_requests = types.SimpleNamespace(Session=Session)

    photos = scratch / 'Photos'
    photos.mkdir()
    leve = None
    try:
        with patched(_get_cookies_file=lambda: str(cookies), PHOTOS_FOLDER=photos,
                     _convert_to_jpg=lambda p: p, requests=faux_requests):
            app._download_instagram_images(entry['queue'],
                                           'https://www.instagram.com/p/ABCDEFG/', entry)
    except app.DownloadAborted as e:
        leve = e.status
    except Exception as e:
        leve = 'AUTRE: %s' % type(e).__name__

    ev = []
    while not entry['queue'].empty():
        ev.append(entry['queue'].get_nowait())
    final = next((e for e in ev if e.get('status') in app.TERMINAL_STATUSES), {})
    images = [g for g in gets if g.startswith('https://img/')]
    shutil.rmtree(scratch, ignore_errors=True)
    return leve, len(images), final.get('status'), NB


def test_instagram_annulation():
    section('Photos Instagram : arret sur le chemin `continue`')
    leve, n, statut, total = _instagram_continue(annuler=True)
    check("l'arret est vu", leve == 'cancelled', 'leve : %s' % leve)
    check("la boucle s'arrete tot", n < total, '%d image(s) sur %d' % (n, total))
    check('aucun evenement terminal trompeur', statut is None, 'publie : %s' % statut)

    # Le controle : sans le drapeau, la meme boucle doit visiter TOUTES les
    # images et finir en erreur. Si les deux runs s'arretaient a deux images,
    # le test mesurerait autre chose que l'annulation.
    leve, n, statut, total = _instagram_continue(annuler=False)
    check('controle : aucun arret leve', leve is None, 'leve : %s' % leve)
    check('controle : toutes les images visitees', n == total, '%d sur %d' % (n, total))
    check("controle : finit en 'error'", statut == 'error', 'publie : %s' % statut)


def _fabrique_source(cible):
    """Une video assez longue pour qu'on ait le temps d'annuler sa decoupe."""
    subprocess.run(
        [app.FFMPEG_PATH, '-y', '-f', 'lavfi', '-i', 'testsrc=size=1280x720:rate=30',
         '-t', '40', '-c:v', 'libx264', '-preset', 'ultrafast', str(cible)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **app._SUBPROCESS_FLAGS)
    return cible.exists()


def _decoupe(mode, modele):
    """Un run de decoupe, annule ou mis en erreur.

    L'erreur est le controle : sur ce chemin la source DOIT etre supprimee.
    Si les deux la gardaient, le test ne mesurerait pas la branche 'cancelled'
    mais un nettoyage devenu inerte.

    Chaque run recoit sa PROPRE copie -- le controle supprime la sienne, c'est
    justement ce qu'il verifie -- mais la video n'est encodee qu'une fois.
    """
    scratch = Path(tempfile.mkdtemp(prefix='cut-'))
    source = scratch / 'source.mp4'
    shutil.copy(modele, source)

    cut_id = 'testcut'
    q = queue.Queue(maxsize=100)
    app.cut_progress[cut_id] = {'queue': q, 'cancelled': False}
    # En mode erreur : une extension que FFmpeg refuse, il sort en code non nul.
    cible = scratch / ('sortie.mp4' if mode == 'cancel' else 'sortie.inconnu')

    t = threading.Thread(target=app._run_ffmpeg_cut,
                         args=(cut_id, source, cible, 0, 40, source), daemon=True)
    t.start()
    if mode == 'cancel':
        limite = time.time() + 20
        while time.time() < limite and not app.cut_progress[cut_id].get('proc'):
            time.sleep(0.05)
        time.sleep(0.5)
        # Par la ROUTE, et non en posant le drapeau a la main : _run_ffmpeg_cut
        # ne relit ce drapeau qu'apres proc.wait(), donc c'est /cancel, via
        # _kill_quietly, qui interrompt reellement FFmpeg. En le posant
        # directement, le test laissait la decoupe aller a son terme et ne
        # verifiait plus que la comptabilite d'apres-coup.
        client.post('/cancel/%s' % cut_id, headers=H)
    t.join(timeout=90)

    ev = []
    while not q.empty():
        ev.append(q.get_nowait())
    final = next((e for e in ev if e.get('status') in app.TERMINAL_STATUSES), {})
    avance = max([e.get('percent', 0) for e in ev if e.get('status') == 'progress'] or [0])
    resultat = (final.get('status'), source.exists(), cible.exists(), avance)
    shutil.rmtree(scratch, ignore_errors=True)
    return resultat


def test_decoupe_annulation():
    section('Decoupe : arreter garde la source')
    atelier = Path(tempfile.mkdtemp(prefix='cut-modele-'))
    modele = atelier / 'modele.mp4'
    if not _fabrique_source(modele):
        check('FFmpeg disponible pour ce test', False, 'source de test non fabriquee')
        shutil.rmtree(atelier, ignore_errors=True)
        return
    statut, source, sortie, avance = _decoupe('cancel', modele)
    check("etat terminal = cancelled", statut == 'cancelled', 'recu : %s' % statut)
    # Le point du test : FFmpeg doit etre INTERROMPU, pas juste comptabilise
    # comme annule. Sans le kill, la coupe atteignait 99,8 %.
    check('FFmpeg est bien interrompu', avance < 50, 'progression max %.1f %%' % avance)
    # On arrete une coupe pour la refaire avec d'autres bornes : effacer la
    # source renvoyait un 404 « Fichier source non trouve » au moment de
    # relancer, apercu mort compris.
    check('la source est conservee', source is True)
    check('aucune sortie partielle laissee', sortie is False)

    statut, source, sortie, _ = _decoupe('erreur', modele)
    check("controle : etat terminal = error", statut == 'error', 'recu : %s' % statut)
    check('controle : la source est supprimee', source is False)
    shutil.rmtree(atelier, ignore_errors=True)


# ===================================================== reseau (--online)
def test_radio_en_ligne():
    section('Radio YouTube, en conditions reelles')
    check("l'URL radio n'est pas vue comme une playlist",
          app.is_playlist_url(RADIO) is False)
    t0 = time.time()
    r = client.post('/get-info', headers=H, json={'url': RADIO})
    dt = time.time() - t0
    d = r.get_json() or {}
    # Sans noplaylist, cet appel ne rendait pas la main en 120 s.
    check('/get-info repond sous 30 s', dt < 30, '%.1f s' % dt)
    check('reponse sans erreur', r.status_code == 200 and not d.get('error'),
          str(d.get('error'))[:50])
    check('resolue comme une video', d.get('is_playlist') is not True)

    t0 = time.time()
    d2 = client.post('/get-info', headers=H,
                     json={'url': 'https://youtu.be/MEKERDMnC48'}).get_json() or {}
    check('le lien court repond toujours', not d2.get('error'), '%.1f s' % (time.time() - t0))
    check('les deux liens donnent la meme video', d.get('title') == d2.get('title'))


def test_annulation_reelle():
    section("Annulation d'un vrai telechargement")
    URL = 'https://download.samplelib.com/mp4/sample-30s.mp4'

    def run(annuler):
        scratch = Path(tempfile.mkdtemp(prefix='cancel-'))
        home = scratch / 'out'
        home.mkdir()
        travail = scratch / 'work'
        travail.mkdir()
        job = 'testjob'
        q = queue.Queue(maxsize=100)
        now = time.time()
        app.download_progress[job] = {'queue': q, 'start_time': now,
                                      'deadline': now + app.DOWNLOAD_TIMEOUT,
                                      'cancelled': False}
        with patched(_get_output_folder=lambda t: home, WORK_FOLDER=travail):
            t = threading.Thread(target=app._run_download,
                                 args=(job, URL, 'social', None), daemon=True)
            t.start()
            # Attendre que le transfert ait vraiment commence : annuler avant
            # qu'un seul hook n'ait tourne ne prouverait rien.
            limite = time.time() + 30
            demarre = False
            while time.time() < limite:
                if any(travail.rglob('*')):
                    demarre = True
                    break
                time.sleep(0.1)
            if annuler:
                app.download_progress[job]['cancelled'] = True
            t.join(timeout=60)
        ev = []
        while not q.empty():
            ev.append(q.get_nowait())
        final = next((e for e in ev if e.get('status') in app.TERMINAL_STATUSES), {})
        res = (demarre, final.get('status'), sorted(p.name for p in home.glob('*')),
               sorted(p.name for p in travail.rglob('*')), t.is_alive())
        shutil.rmtree(scratch, ignore_errors=True)
        return res

    demarre, statut, produits, restes, vivant = run(annuler=True)
    check('le transfert a bien demarre', demarre)
    check("etat terminal = cancelled", statut == 'cancelled', 'recu : %s' % statut)
    check('aucun fichier final produit', not produits, str(produits))
    check('dossier de travail nettoye', not restes, str(restes))
    check('le thread est termine', not vivant)

    demarre, statut, produits, restes, vivant = run(annuler=False)
    check("controle : etat terminal = complete", statut == 'complete', 'recu : %s' % statut)
    check('controle : fichier produit', bool(produits), str(produits))
    check('controle : dossier de travail nettoye', not restes, str(restes))


# ===================================================== execution
HORS_LIGNE = [test_origine, test_sec_fetch_site, test_sanitize, test_comportements,
              test_echeances, test_playlist_repousse, test_routage,
              test_photo_sans_audio, test_playlist_compte_partiel, test_photos,
              test_route_cancel, test_instagram_annulation, test_decoupe_annulation]
EN_LIGNE = [test_radio_en_ligne, test_annulation_reelle]


def main():
    for t in HORS_LIGNE + (EN_LIGNE if ONLINE else []):
        t()
        # Les deux registres sont un etat global au meme titre que les
        # attributs que `patched` restaure : les laisser peuples ferait de
        # l'ordre des sections un contrat tacite.
        app.download_progress.clear()
        app.cut_progress.clear()
        # Le limiteur de debit en est un troisieme : au-dela de 10 POST par
        # minute, une section prendrait un 429 etranger a son sujet.
        app._request_times.clear()
    if not ONLINE:
        print('\n  (tests reseau sautes ; --online pour les inclure)')

    total, echecs = len(_resultats), _resultats.count(False)
    print('\n%d assertions, %d echec(s)' % (total, echecs))
    if total == 0:
        # Une suite qui ne mesure rien ne doit jamais sortir en 0 : c'est comme
        # cela qu'un test casse a pu passer pour vert.
        print("AUCUNE ASSERTION N'A TOURNE")
        return 2
    return 1 if echecs else 0


if __name__ == '__main__':
    sys.exit(main())
