<?php
// Host-owned seam: return the existing verified school HTTP dispatcher.
// No shared token, browser-supplied identity, new login, or public preview proxy.
return isset($GLOBALS['bridgeSpotBackendDispatch']) &&
       is_callable($GLOBALS['bridgeSpotBackendDispatch'])
    ? $GLOBALS['bridgeSpotBackendDispatch'] : null;
