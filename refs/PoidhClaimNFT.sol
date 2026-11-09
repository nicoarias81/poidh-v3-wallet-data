// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {
  ERC721URIStorage
} from "@openzeppelin/contracts/token/ERC721/extensions/ERC721URIStorage.sol";
import {IPoidhClaimNFT} from "./interfaces/IPoidhClaimNFT.sol";

/// @title PoidhClaimNFT
/// @notice Minimal ERC721 for POIDH claim NFTs.
/// @dev Designed to avoid callback-based reentrancy:
/// - Uses `_mint` (not `_safeMint`).
/// - PoidhV3 mints to itself as escrow, then transfers with `transferFrom` (not
/// `safeTransferFrom`).
contract PoidhClaimNFT is ERC721URIStorage, IPoidhClaimNFT {
  /// @dev Reverts for unauthorized mint attempts.
  error NotPoidh();
  /// @dev Reverts when wiring to address(0).
  error InvalidPoidhAddress();

  /// @notice The PoidhV3 contract authorized to mint claim NFTs.
  address public immutable poidh;

  /// @param name_ ERC721 name.
  /// @param symbol_ ERC721 symbol.
  /// @param poidh_ The PoidhV3 contract that will mint claim NFTs.
  constructor(string memory name_, string memory symbol_, address poidh_) ERC721(name_, symbol_) {
    if (poidh_ == address(0)) revert InvalidPoidhAddress();
    poidh = poidh_;
  }

  /// @inheritdoc IPoidhClaimNFT
  function mintToEscrow(uint256 tokenId, string calldata uri) external {
    _revertIfNotPoidh();
    _mint(poidh, tokenId);
    _setTokenURI(tokenId, uri);
  }

  function _revertIfNotPoidh() internal view {
    if (msg.sender != poidh) revert NotPoidh();
  }
}
