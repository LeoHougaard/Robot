package com.leo.pixelrobot.policy

import kotlin.math.sqrt

internal fun matVec(matrix: Array<FloatArray>, vector: FloatArray): FloatArray =
    FloatArray(matrix.size) { row ->
        var value = 0f
        for (column in vector.indices) value += matrix[row][column] * vector[column]
        value
    }

internal fun norm(vector: FloatArray): Float = sqrt(vector.sumOf { (it * it).toDouble() }).toFloat()

internal fun cross(left: FloatArray, right: FloatArray): FloatArray = floatArrayOf(
    left[1] * right[2] - left[2] * right[1],
    left[2] * right[0] - left[0] * right[2],
    left[0] * right[1] - left[1] * right[0],
)

