// TrackInfoLoader - YamaPlayer module for cympfh/video
//
// s.cympfh.cc 経由で再生したトラックのタイトル (とアートワーク) を
// /video/info エンドポイントから取ってきて YamaPlayer に表示する。
// YamaPlayer 本体の改造は不要。ワールドに GameObject として置くだけでよい。
//
// 使い方は yamaplayer/README.md を参照。

using UdonSharp;
using UnityEngine;
using UnityEngine.Networking;
using VRC.SDK3.Data;
using VRC.SDK3.Image;
using VRC.SDK3.StringLoading;
using VRC.SDKBase;
using VRC.Udon.Common.Interfaces;

namespace Yamadev.YamaStream.Modules.TrackInfoLoader
{
  [UdonBehaviourSyncMode(BehaviourSyncMode.None)]
  public class TrackInfoLoader : YamaPlayerModule
  {
    [Header("video server")]
    [SerializeField] private string _serverBase = "https://s.cympfh.cc";

    [Header("Thumbnail (optional)")]
    [Tooltip("アートワークを表示したい Renderer (Quad など)。空ならタイトルだけ設定する")]
    [SerializeField] private Renderer _thumbnailTarget;
    [SerializeField] private string _thumbnailProperty = "_MainTex";

    private string _base;                         // _serverBase の末尾 / を除いたもの
    private DataDictionary _cache = new DataDictionary();   // urlStr => title
    private DataDictionary _pending = new DataDictionary(); // urlStr => true
    private DataDictionary _error = new DataDictionary();   // urlStr => true
    private VRCImageDownloader _imageDownloader;

    public virtual void Start()
    {
      _base = _serverBase;
      while (_base.EndsWith("/")) _base = _base.Substring(0, _base.Length - 1);

      // YamaPlayerModule.Start は _controller が SerializeField で
      // 設定済みならリスナー登録する。未設定ならシーンから探す。
      if (!Utilities.IsValid(_controller))
      {
        _controller = (Controller)Object.FindObjectOfType(typeof(Controller));
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

    private void LoadInfo(string urlStr)
    {
      if (string.IsNullOrEmpty(urlStr)) return;
      if (!urlStr.StartsWith(_base)) return; // このサーバー経由の再生だけ
      if (_cache.ContainsKey(urlStr) || _pending.ContainsKey(urlStr) || _error.ContainsKey(urlStr)) return;

      _pending.SetValue(urlStr, true);
      VRCStringDownloader.LoadUrl(new VRCUrl(InfoEndpoint(urlStr)), (IUdonEventReceiver)this);
    }

    // リクエストした info URL から元の再生 URL を復元する。関係ないロードは null を返す
    private string OriginalUrlOf(string requested)
    {
      string prefix = _base + "/video/info?url=";
      if (!requested.StartsWith(prefix)) return null;
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

      UpdateVideoInfoToTrack(urlStr, title);
      UpdateVideoInfoToQueue(urlStr, title);

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

    private void UpdateVideoInfoToTrack(string urlStr, string title)
    {
      if (!Utilities.IsValid(_controller)) return;

      var track = _controller.Track;
      if (!Utilities.IsValid(track) || TrackUtils.GetUrl(track).Get() != urlStr) return;
      if (!string.IsNullOrEmpty(TrackUtils.GetTitle(track))) return;

      TrackUtils.SetTitle(track, title);
      _controller.SendCustomVideoEvent(nameof(AfterTrackUpdated));
    }

    private void UpdateVideoInfoToQueue(string urlStr, string title)
    {
      if (!Utilities.IsValid(_controller)) return;

      var queue = _controller.Queue;
      if (!Utilities.IsValid(queue) || queue.TrackCount == 0) return;

      int trackCount = queue.TrackCount;
      int changedCount = 0;
      for (int i = 0; i < trackCount; i++)
      {
        var track = queue.GetTrack(i);
        if (!Utilities.IsValid(track)) continue;
        if (TrackUtils.GetUrl(track).Get() != urlStr) continue;
        if (!string.IsNullOrEmpty(TrackUtils.GetTitle(track))) continue;

        TrackUtils.SetTitle(track, title);
        changedCount++;
      }

      if (changedCount > 0)
      {
        _controller.SendCustomVideoEvent(nameof(AfterQueueUpdated));
      }
    }

    public override void AfterTrackLoaded()
    {
      if (!Utilities.IsValid(_controller)) return;

      var track = _controller.Track;
      if (!Utilities.IsValid(track) || track.Length == 0) return;
      if (!string.IsNullOrEmpty(TrackUtils.GetTitle(track))) return;

      string urlStr = TrackUtils.GetUrl(track).Get();
      if (_cache.TryGetValue(urlStr, TokenType.String, out DataToken cached))
      {
        TrackUtils.SetTitle(track, cached.String);
        _controller.SendCustomVideoEvent(nameof(AfterTrackUpdated));
        return;
      }
      LoadInfo(urlStr);
    }

    public override void AfterQueueUpdated()
    {
      if (!Utilities.IsValid(_controller)) return;

      var queue = _controller.Queue;
      if (!Utilities.IsValid(queue)) return;

      int trackCount = queue.TrackCount;
      int changedCount = 0;
      for (int i = 0; i < trackCount; i++)
      {
        var track = queue.GetTrack(i);
        if (!Utilities.IsValid(track)) continue;
        if (!string.IsNullOrEmpty(TrackUtils.GetTitle(track))) continue;

        var url = TrackUtils.GetUrl(track);
        if (!Utilities.IsValid(url)) continue;
        string urlStr = url.Get();

        if (_cache.TryGetValue(urlStr, TokenType.String, out DataToken cached))
        {
          TrackUtils.SetTitle(track, cached.String);
          changedCount++;
        }
        else
        {
          LoadInfo(urlStr);
        }
      }

      if (changedCount > 0)
      {
        _controller.SendCustomVideoEvent(nameof(AfterQueueUpdated));
      }
    }
  }
}
