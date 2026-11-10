// TrackInfoLoader - YamaPlayer 1.5.x 用パッチ (cympfh/video)
//
// s.cympfh.cc 経由で再生したトラックのタイトル (とアートワーク) を
// /video/info エンドポイントから取ってきて YamaPlayer に表示する。
// YamaPlayer 本体の改造は不要。ワールドに GameObject として置くだけでよい。
//
// 標準の VideoInfo は YouTube / Twitch にしか対応していないが、
// このワールドでは全入力が s.cympfh.cc 経由になるため別途用意した。
//
// 使い方は yamaplayer/README.md (https://github.com/cympfh/video) を参照。

using UdonSharp;
using UnityEngine;
using UnityEngine.Networking;
using VRC.SDK3.Data;
using VRC.SDK3.Image;
using VRC.SDK3.StringLoading;
using VRC.SDKBase;
using VRC.Udon.Common.Interfaces;

namespace Yamadev.YamaStream.Patch
{
  [UdonBehaviourSyncMode(BehaviourSyncMode.None)]
  public class TrackInfoLoader : Listener
  {
    [Header("video server")]
    [SerializeField] private string _serverBase = "https://s.cympfh.cc";

    [Header("Thumbnail (optional)")]
    [Tooltip("アートワークを表示したい Renderer (Quad など)。空ならタイトルだけ設定する")]
    [SerializeField] private Renderer _thumbnailTarget;
    [SerializeField] private string _thumbnailProperty = "_MainTex";

    [SerializeField] private Controller _controller;

    private string _base;                           // _serverBase の末尾 / を除いたもの
    private DataDictionary _cache = new DataDictionary();   // urlStr => title
    private DataDictionary _pending = new DataDictionary(); // urlStr => true
    private DataDictionary _error = new DataDictionary();   // urlStr => true
    private VRCImageDownloader _imageDownloader;

    private void Start()
    {
      _base = _serverBase;
      while (_base.EndsWith("/")) _base = _base.Substring(0, _base.Length - 1);

      if (!Utilities.IsValid(_controller))
      {
        _controller = (Controller)FindObjectOfType(typeof(Controller));
      }
      if (!Utilities.IsValid(_controller))
      {
        PrintError("Controller is not found for TrackInfoLoader");
        return;
      }
      _controller.AddListener(this);
      _imageDownloader = new VRCImageDownloader();
    }

    private string InfoEndpoint(string urlStr)
    {
      return _base + "/video/info?url=" + UnityWebRequest.EscapeURL(urlStr);
    }

    // このサーバー経由の URL か
    private bool IsOurUrl(string urlStr)
    {
      return !string.IsNullOrEmpty(urlStr) && urlStr.StartsWith(_base);
    }

    private void LoadInfo(string urlStr)
    {
      if (!IsOurUrl(urlStr)) return;
      if (_cache.ContainsKey(urlStr) || _pending.ContainsKey(urlStr) || _error.ContainsKey(urlStr)) return;

      _pending.SetValue(urlStr, true);
      VRCStringDownloader.LoadUrl(new VRCUrl(InfoEndpoint(urlStr)), (IUdonEventReceiver)this);
    }

    // リクエストした info URL から元の再生 URL を復元する。関係ないロードは null を返す
    private string OriginalUrlOf(string requested)
    {
      string prefix = _base + "/video/info?url=";
      if (string.IsNullOrEmpty(requested) || !requested.StartsWith(prefix)) return null;
      return UnityWebRequest.UnEscapeURL(requested.Substring(prefix.Length));
    }

    public override void OnStringLoadSuccess(IVRCStringDownload result)
    {
      string urlStr = OriginalUrlOf(result.Url.Get());
      if (urlStr == null) return;
      _pending.Remove(urlStr);

      if (!VRCJson.TryDeserializeFromJson(result.Result, out DataToken json) ||
          json.TokenType != TokenType.DataDictionary)
      {
        _error.SetValue(urlStr, true);
        return;
      }
      DataDictionary info = json.DataDictionary;
      if (!info.TryGetValue("title", TokenType.String, out DataToken titleToken) ||
          string.IsNullOrEmpty(titleToken.String))
      {
        _error.SetValue(urlStr, true);
        return;
      }

      string title = titleToken.String;
      _cache.SetValue(urlStr, title);
      PrintLog($"Loaded track info: {title} ({urlStr})");

      // 現在のトラックに設定
      var track = _controller.Track;
      if (track.IsValid() && !track.HasTitle() && track.GetUrl() == urlStr)
      {
        track.SetTitle(title);
        _controller.SendCustomVideoEvent(nameof(OnVideoInfoLoaded));
      }
      // キュー内の同じ URL にも設定
      ApplyToQueue();

      if (Utilities.IsValid(_thumbnailTarget))
      {
        _imageDownloader.DownloadImage(
          new VRCUrl(_base + "/video/thumbnail?url=" + UnityWebRequest.EscapeURL(urlStr)),
          null, (IUdonEventReceiver)this, null);
      }
    }

    public override void OnStringLoadError(IVRCStringDownload result)
    {
      string urlStr = OriginalUrlOf(result.Url.Get());
      if (urlStr == null) return;
      _pending.Remove(urlStr);
      _error.SetValue(urlStr, true);
      PrintLog($"Failed to load track info: {urlStr}");
    }

    public override void OnImageLoadSuccess(IVRCImageDownload result)
    {
      if (Utilities.IsValid(_thumbnailTarget) && result.Result != null)
      {
        _thumbnailTarget.material.SetTexture(_thumbnailProperty, result.Result);
      }
    }

    public override void OnImageLoadError(IVRCImageDownload result)
    {
      PrintLog($"Failed to load thumbnail: {result.ErrorMessage}");
    }

    // 新しいトラックが再生された
    public override void OnUrlChanged()
    {
      if (!Utilities.IsValid(_controller)) return;

      var track = _controller.Track;
      if (!track.IsValid() || track.HasTitle()) return;

      string urlStr = track.GetUrl();
      if (_cache.TryGetValue(urlStr, TokenType.String, out DataToken cached) &&
          !string.IsNullOrEmpty(cached.String))
      {
        track.SetTitle(cached.String);
        _controller.SendCustomVideoEvent(nameof(OnVideoInfoLoaded));
        return;
      }
      LoadInfo(urlStr);
    }

    // キューのタイトル未設定エントリに、キャッシュ済みのタイトルを設定し、
    // 未取得のものは取得を開始する
    private void ApplyToQueue()
    {
      if (!Utilities.IsValid(_controller)) return;

      var queue = _controller.Queue;
      if (!Utilities.IsValid(queue)) return;

      int trackCount = queue.Length;
      bool changed = false;
      for (int i = 0; i < trackCount; i++)
      {
        var track = queue.GetTrack(i);
        if (!track.IsValid() || track.HasTitle()) continue;

        string urlStr = track.GetUrl();
        if (_cache.TryGetValue(urlStr, TokenType.String, out DataToken cached) &&
            !string.IsNullOrEmpty(cached.String))
        {
          track.SetTitle(cached.String);
          changed = true;
        }
        else
        {
          LoadInfo(urlStr);
        }
      }

      if (changed)
      {
        _controller.SendCustomVideoEvent(nameof(OnQueueUpdated));
      }
    }

    public override void OnQueueUpdated()
    {
      ApplyToQueue();
    }
  }
}
