package com.leo.pixelrobot.policy

import android.content.res.AssetManager

/** Immutable bytes shared by the physical runtime and shadow identity validator.
 * Loading/identifying a bundle grants no authority to run it. */
class LoadedPolicyAssets private constructor(
    metadata: ByteArray,
    manifest: ByteArray,
    actor: ByteArray,
    reference: ByteArray?,
    calibration: ByteArray,
) {
    private val metadataBytes = metadata.copyOf()
    private val manifestBytes = manifest.copyOf()
    private val actorBytes = actor.copyOf()
    private val referenceBytes = reference?.copyOf()
    private val calibrationBytes = calibration.copyOf()
    // PolicyContract currently exposes mutable arrays. Never share one parsed instance
    // between the service and controller or allow callers to mutate our byte-bound view.
    val contract: PolicyContract get() = PolicyContract.parse(metadataBytes.toString(Charsets.UTF_8)).also {
        if (it.usesStrideReference) it.requireLiveActivationCandidate()
    }
    init { contract }
    val calibrationJson: String get() = calibrationBytes.toString(Charsets.UTF_8)
    fun actor(): ByteArray = actorBytes.copyOf()
    fun manifest(): ByteArray = manifestBytes.copyOf()
    fun reference(): ByteArray? = referenceBytes?.copyOf()

    fun shadowCapabilities(): ShadowCapabilities = ShadowCapabilities.fromBundle(
        metadataBytes, manifestBytes, actorBytes,
        requireNotNull(referenceBytes) { "shadow_stride_reference_required" }, calibrationBytes,
    )

    fun physicalBinding(): PhysicalPolicyBinding = PhysicalPolicyBinding.fromBundle(shadowCapabilities(), metadataBytes)

    companion object {
        fun fromBytes(metadata: ByteArray, manifest: ByteArray, actor: ByteArray,
                      reference: ByteArray?, calibration: ByteArray): LoadedPolicyAssets =
            LoadedPolicyAssets(metadata, manifest, actor, reference, calibration)

        fun load(assets: AssetManager, calibrationOverrideJson: String? = null): LoadedPolicyAssets {
            val metadata = assets.open("policy_metadata.json").use { it.readBytes() }
            val contract = PolicyContract.parse(metadata.toString(Charsets.UTF_8))
            require(contract.profileId.matches(Regex("[a-z0-9][a-z0-9-]{0,63}")))
            return fromBytes(
                metadata = metadata,
                manifest = assets.open("policy_android_manifest.json").use { it.readBytes() },
                actor = assets.open("policy_actor.onnx").use { it.readBytes() },
                reference = if (contract.usesStrideReference) assets.open("stride_reference.json").use { it.readBytes() } else null,
                calibration = calibrationOverrideJson?.toByteArray(Charsets.UTF_8)
                    ?: assets.open("${contract.profileId}.calibration.json").use { it.readBytes() },
            )
        }
    }
}
